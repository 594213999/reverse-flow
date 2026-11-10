#Requires -Version 5.1
<#
.SYNOPSIS
    REA Evidence -> reverse-skill Evidence 桥。

.DESCRIPTION
    把 REA（rea-agents）输出的 Evidence JSON 转换成 reverse-skill 的
    work/<case>/evidence/E-*.md 记录。

    设计原则：本脚本只做「格式适配」，不重新实现证据契约。
    实际写入一律委托官方 skills/scripts/append-evidence.ps1，
    从而自动继承它的不可变写入、字段校验与 INDEX.md 维护行为。

    因此 reverse-skill 升级后，只要 append-evidence.ps1 的接口不变，本桥无需改动。

.PARAMETER CaseRoot
    reverse-skill 的 case 目录（含 evidence/ 子目录），例如 work/rea-ariang。

.PARAMETER ReaJson
    REA 输出的 JSON 文件。推荐用 --filter-output 生成精简输出：
      npx -y rea-agents@6.7.0 analyze-javascript-application <path> --format json `
        --filter-output evidence_id,subject,provider,operation,confidence,authority,limitations,normalized_result.summary,normalized_result.statistics,normalized_result.semantic_graph.coverage

.PARAMETER Id
    Evidence 编号（如 E-003）。省略则自动取 evidence/ 下最大编号 +1。

.PARAMETER Severity
    默认 n/a_re（reverse-skill 为纯逆向证据保留的严重度）。

.PARAMETER Status
    默认 observed（REA 结论是静态观察，不是已验证漏洞）。

.PARAMETER SourceType
    默认 file（REA 证据的对象是产物文件，content_hash 也是产物摘要）。
    可取值必须是 reverse-skill 枚举：command | screenshot | file | log | memory | network | manual

.PARAMETER LinkedWorkitem
    关联的 work item（WI-xxx）。append-evidence.ps1 不支持该参数，
    因此写入后由本桥做一次精确单行替换；省略则保持 n/a。

.PARAMETER MaxUnknowns
    raw_excerpt 里保留的 unknown 样例条数（默认 5）。REA 的 unknowns 可达数万条，
    只保留样例，其余计数记录。

.PARAMETER MaxJsonChars
    单行 JSON 上限（默认 16 MiB）。超过则拒绝，避免把 200MB 级输出喂进内存；
    遇到该错误请用 --filter-output 重新生成。

.EXAMPLE
    .\rea-evidence-bridge.ps1 -CaseRoot work/rea-ariang -ReaJson .\ariang.evidence.json

.EXAMPLE
    .\rea-evidence-bridge.ps1 -CaseRoot work/rea-ariang -ReaJson .\a.json -LinkedWorkitem WI-002 -DryRun
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $CaseRoot,

    [Parameter(Mandatory = $true)]
    [string] $ReaJson,

    [string] $Id = '',
    [string] $Title = '',
    [string] $Severity = 'n/a_re',
    [string] $Status = 'observed',
    [string] $SourceType = 'file',
    [string] $LinkedWorkitem = 'n/a',
    [string] $Location = '',
    [string] $AppendEvidenceScript = '',
    [int] $MaxUnknowns = 5,
    [int] $MaxJsonChars = 16 * 1024 * 1024,
    [string] $TimelineRole = 'lead',
    [switch] $NoTimeline,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Step([string] $message) {
    Write-Host ("[rea-bridge] {0}" -f $message) -ForegroundColor Cyan
}

function Get-DeepValue($object, [string] $path) {
    $current = $object
    foreach ($segment in $path.Split('.')) {
        if ($null -eq $current) { return $null }
        if ($current -is [System.Collections.IDictionary]) {
            if (-not $current.Contains($segment)) { return $null }
            $current = $current[$segment]
            continue
        }
        $property = $current.PSObject.Properties[$segment]
        if ($null -eq $property) { return $null }
        $current = $property.Value
    }
    return $current
}

function ConvertTo-SingleLine([string] $text) {
    if ([string]::IsNullOrWhiteSpace($text)) { return 'n/a' }
    $t = $text -replace '[\x00-\x1F]', ' '
    $t = $t -replace '["""]', "'"
    $t = $t -replace '\s+', ' '
    return $t.Trim()
}

function Format-Scalar($value) {
    if ($null -eq $value) { return 'n/a' }
    if ($value -is [bool]) { if ($value) { return 'true' } else { return 'false' } }
    return [string]$value
}

# 读写保留原文件 BOM 与行尾，避免把 case 文件改出无谓 diff。
function Read-TextPreserving([string] $path) {
    $bytes = [System.IO.File]::ReadAllBytes($path)
    $bom = ($bytes.Length -ge 3 -and $bytes[0] -eq 0xEF -and $bytes[1] -eq 0xBB -and $bytes[2] -eq 0xBF)
    $text = [System.IO.File]::ReadAllText($path, [System.Text.Encoding]::UTF8)
    $newline = "`n"
    if ($text.Contains("`r`n")) { $newline = "`r`n" }
    return @{ Text = $text; Bom = $bom; Newline = $newline }
}

function Write-TextPreserving([string] $path, [string] $text, [bool] $bom) {
    [System.IO.File]::WriteAllText($path, $text, (New-Object System.Text.UTF8Encoding $bom))
}

# ---------------------------------------------------------------- 1. 参数与路径

if (-not (Test-Path -LiteralPath $CaseRoot -PathType Container)) {
    throw "CaseRoot 不存在: $CaseRoot"
}
$caseFull = (Resolve-Path -LiteralPath $CaseRoot).Path
$evidenceDir = Join-Path $caseFull 'evidence'
if (-not (Test-Path -LiteralPath $evidenceDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $evidenceDir | Out-Null
    Write-Step "已创建 evidence/ 目录"
}
if (-not (Test-Path -LiteralPath $ReaJson -PathType Leaf)) {
    throw "ReaJson 不存在: $ReaJson"
}

$allowedSeverity = @('critical', 'high', 'medium', 'low', 'info', 'n/a', 'n/a_re')
$allowedStatus = @('observed', 'candidate', 'validated', 'false_positive', 'accepted_risk')
$allowedSourceType = @('command', 'screenshot', 'file', 'log', 'memory', 'network', 'manual')
if ($allowedSeverity -notcontains $Severity.ToLowerInvariant()) {
    throw ("-Severity 非法: {0}（允许: {1}）" -f $Severity, ($allowedSeverity -join ', '))
}
if ($allowedStatus -notcontains $Status.ToLowerInvariant()) {
    throw ("-Status 非法: {0}（允许: {1}）" -f $Status, ($allowedStatus -join ', '))
}
if ($allowedSourceType -notcontains $SourceType.ToLowerInvariant()) {
    throw ("-SourceType 非法: {0}（允许: {1}）" -f $SourceType, ($allowedSourceType -join ', '))
}

# 定位官方 append-evidence.ps1（不硬编码用户名）
if ([string]::IsNullOrWhiteSpace($AppendEvidenceScript)) {
    $candidates = @(
        (Join-Path $env:USERPROFILE '.reasonix\skills\reverse-skill\skills\scripts\append-evidence.ps1'),
        (Join-Path (Split-Path -Parent (Split-Path -Parent $caseFull)) 'skills\scripts\append-evidence.ps1')
    )
    foreach ($candidate in $candidates) {
        if ($candidate -and (Test-Path -LiteralPath $candidate -PathType Leaf)) {
            $AppendEvidenceScript = $candidate
            break
        }
    }
}
if (-not $AppendEvidenceScript -or -not (Test-Path -LiteralPath $AppendEvidenceScript -PathType Leaf)) {
    throw "找不到 append-evidence.ps1。请用 -AppendEvidenceScript 指定 reverse-skill 的 skills/scripts/append-evidence.ps1"
}

# ------------------------------------------------------------- 2. 读取 REA JSON

Write-Step ("读取 " + (Split-Path -Leaf $ReaJson))
$reader = [System.IO.StreamReader]::new((Resolve-Path -LiteralPath $ReaJson).Path, [System.Text.Encoding]::UTF8)
$lastLine = $null
$lineCount = 0
$progressCount = 0
try {
    while ($null -ne ($line = $reader.ReadLine())) {
        $lineCount++
        if ($line.IndexOf('"rea_progress"') -ge 0) { $progressCount++; continue }
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        if ($line.Length -gt $MaxJsonChars) {
            throw ("证据记录单行 {0} 字符，超过上限 {1}。REA 会把完整 Evidence 一次吐出（实测可达 200MB+）；请加 --filter-output 只取需要字段后重跑。" -f $line.Length, $MaxJsonChars)
        }
        $lastLine = $line
    }
} finally {
    $reader.Dispose()
}
if (-not $lastLine) { throw "在 $ReaJson 中找不到 Evidence 记录（只找到 $progressCount 行进度输出）" }
Write-Step ("行数 {0}（进度 {1}），证据 {2} 字节" -f $lineCount, $progressCount, $lastLine.Length)

try {
    $evidence = $lastLine | ConvertFrom-Json
} catch {
    throw ("Evidence JSON 解析失败: {0}" -f $_.Exception.Message)
}

# ------------------------------------------------------------- 3. 提取字段

$reaEvidenceId = Format-Scalar (Get-DeepValue $evidence 'evidence_id')
$operation = Format-Scalar (Get-DeepValue $evidence 'operation')
$predicateType = Format-Scalar (Get-DeepValue $evidence 'predicate_type')
$confidence = Format-Scalar (Get-DeepValue $evidence 'confidence')
$authority = Format-Scalar (Get-DeepValue $evidence 'authority')

$subjectName = Format-Scalar (Get-DeepValue $evidence 'subject.name')
$subjectFormat = Format-Scalar (Get-DeepValue $evidence 'subject.format')
$subjectSha = Format-Scalar (Get-DeepValue $evidence 'subject.digest.sha256')
$subjectPath = Format-Scalar (Get-DeepValue $evidence 'subject.local_path')
if ($subjectPath -eq 'n/a') { $subjectPath = Format-Scalar (Get-DeepValue $evidence 'normalized_result.input_path') }

$providerId = Format-Scalar (Get-DeepValue $evidence 'provider.id')
$providerName = Format-Scalar (Get-DeepValue $evidence 'provider.name')
$providerVersion = Format-Scalar (Get-DeepValue $evidence 'provider.version')

$topLimitations = @()
$rawTop = Get-DeepValue $evidence 'limitations'
if ($rawTop) { $topLimitations = @($rawTop) }

$statistics = Get-DeepValue $evidence 'normalized_result.statistics'
$summary = Get-DeepValue $evidence 'normalized_result.summary'
$graphCoverage = Get-DeepValue $evidence 'normalized_result.graph.coverage'
$semanticCoverage = Get-DeepValue $evidence 'normalized_result.semantic_graph.coverage'
$unknownSamples = @(Get-DeepValue $evidence 'normalized_result.semantic_graph.unknowns')
if ($unknownSamples.Count -gt 0 -and $null -eq $unknownSamples[0]) { $unknownSamples = @() }

# --------------------------------------------------- 4. 组装 excerpt / notes / repro

$excerpt = New-Object System.Text.StringBuilder
$null = $excerpt.AppendLine("REA Evidence（结构化摘要；完整记录见 rea_evidence_id）")
$null = $excerpt.AppendLine("operation: $operation")
$null = $excerpt.AppendLine("predicate_type: $predicateType")
$null = $excerpt.AppendLine("subject: [$subjectFormat] $subjectPath")
$null = $excerpt.AppendLine("subject_sha256: $subjectSha")
$null = $excerpt.AppendLine("provider: $providerName ($providerId v$providerVersion)")
$null = $excerpt.AppendLine("confidence/authority: $confidence / $authority")

if ($statistics) {
    $null = $excerpt.AppendLine('')
    $null = $excerpt.AppendLine('statistics:')
    foreach ($property in $statistics.PSObject.Properties) {
        $null = $excerpt.AppendLine(("  {0}: {1}" -f $property.Name, (Format-Scalar $property.Value)))
    }
}

if ($summary) {
    $null = $excerpt.AppendLine('')
    $null = $excerpt.AppendLine('summary:')
    foreach ($property in $summary.PSObject.Properties) {
        $value = $property.Value
        if ($null -ne $value -and $value.PSObject.Properties.Count -gt 0 -and -not ($value -is [string])) {
            $null = $excerpt.AppendLine(("  {0}:" -f $property.Name))
            foreach ($nested in $value.PSObject.Properties) {
                $null = $excerpt.AppendLine(("    {0}: {1}" -f $nested.Name, (Format-Scalar $nested.Value)))
            }
        } else {
            $null = $excerpt.AppendLine(("  {0}: {1}" -f $property.Name, (Format-Scalar $value)))
        }
    }
}

foreach ($pair in @(@('graph.coverage', $graphCoverage), @('semantic_graph.coverage', $semanticCoverage))) {
    $label = $pair[0]
    $coverage = $pair[1]
    if (-not $coverage) { continue }
    $null = $excerpt.AppendLine('')
    $null = $excerpt.AppendLine("${label}:")
    $null = $excerpt.AppendLine(("  status: {0}  truncated: {1}" -f (Format-Scalar (Get-DeepValue $coverage 'status')), (Format-Scalar (Get-DeepValue $coverage 'truncated'))))
    $limits = Get-DeepValue $coverage 'limits'
    if ($limits) {
        foreach ($limit in @($limits)) {
            $null = $excerpt.AppendLine(("  limit: {0} = {1} {2}" -f (Format-Scalar (Get-DeepValue $limit 'name')), (Format-Scalar (Get-DeepValue $limit 'value')), (Format-Scalar (Get-DeepValue $limit 'unit'))))
        }
    }
    $families = Get-DeepValue $coverage 'families'
    if ($families) {
        $parts = @()
        foreach ($family in @($families)) {
            $parts += ("{0}={1}" -f (Format-Scalar (Get-DeepValue $family 'family')), (Format-Scalar (Get-DeepValue $family 'status')))
        }
        $null = $excerpt.AppendLine(("  families: " + ($parts -join ', ')))
    }
}

if ($unknownSamples.Count -gt 0) {
    $null = $excerpt.AppendLine('')
    $null = $excerpt.AppendLine(("unknowns 样例（前 {0} 条，总计见 semantic_graph.coverage）:" -f [Math]::Min($MaxUnknowns, $unknownSamples.Count)))
    $index = 0
    foreach ($unknown in $unknownSamples) {
        if ($index -ge $MaxUnknowns) { break }
        $index++
        $loc = Get-DeepValue $unknown 'evidence.location.value'
        $where = 'n/a'
        if ($loc) {
            if ((Format-Scalar (Get-DeepValue $loc 'kind')) -eq 'source-range') {
                $where = ("{0}:{1}:{2}" -f (Format-Scalar (Get-DeepValue $loc 'source')), (Format-Scalar (Get-DeepValue $loc 'start.line')), (Format-Scalar (Get-DeepValue $loc 'start.column')))
            } else {
                $where = Format-Scalar (Get-DeepValue $loc 'path')
            }
        }
        $null = $excerpt.AppendLine(("  - [{0}] {1} @ {2}" -f (Format-Scalar (Get-DeepValue $unknown 'reason')), (Format-Scalar (Get-DeepValue $unknown 'detail')), $where))
    }
}

$notes = New-Object System.Text.StringBuilder
$null = $notes.AppendLine('由 rea-evidence-bridge.ps1 从 REA 输出转换生成；写入走官方 append-evidence.ps1。')
$null = $notes.AppendLine('来源身份（可用于复核，勿删）:')
$null = $notes.AppendLine("  rea_evidence_id: $reaEvidenceId")
$null = $notes.AppendLine("  rea_operation: $operation")
$null = $notes.AppendLine("  subject_sha256: $subjectSha")
$null = $notes.AppendLine("  provider: $providerId v$providerVersion")
$null = $notes.AppendLine("  confidence/authority: $confidence / $authority")
$null = $notes.AppendLine('')
$null = $notes.AppendLine('离线限制（offline）：该 Evidence 来自本地产物的静态分析。')
$null = $notes.AppendLine('REA 未执行、未加载目标程序，运行时行为未被观察；')
$null = $notes.AppendLine('因此本记录只能支持「静态结构存在」类结论，不能单独支撑「已验证漏洞」。')
if ($topLimitations.Count -gt 0) {
    $null = $notes.AppendLine('')
    $null = $notes.AppendLine('REA limitations（原文，全部保留）:')
    foreach ($limitation in $topLimitations) {
        $null = $notes.AppendLine(("  - {0}" -f (ConvertTo-SingleLine ([string]$limitation))))
    }
}

$cliCommand = ($operation -replace '_', '-')
$repro = @"
# 重放该 Evidence（需 Node 22.19+/24.11+/26+；25.x 不受支持）
npx -y rea-agents@6.7.0 $cliCommand "$subjectPath" --format json --filter-output evidence_id,subject,provider,operation,confidence,authority,limitations,normalized_result.summary,normalized_result.statistics,normalized_result.semantic_graph.coverage
# 若子命令名与上面不符，以「npx -y rea-agents@6.7.0 --help」为准；本记录的 rea_operation 已原样保存在 notes 中。
# 注意：不加 --filter-output 时 REA 会输出完整 Evidence（实测可达 212MB 单行 JSON）。
"@

# ------------------------------------------------------------- 5. 分配 Evidence ID

function Get-NextEvidenceId([string] $dir) {
    $max = 0
    foreach ($file in Get-ChildItem -LiteralPath $dir -Filter 'E-*.md' -File -ErrorAction SilentlyContinue) {
        $m = [regex]::Match($file.BaseName, '^E-(\d+)$')
        if ($m.Success) {
            $value = [int]$m.Groups[1].Value
            if ($value -gt $max) { $max = $value }
        }
    }
    return ('E-{0:d3}' -f ($max + 1))
}

if ([string]::IsNullOrWhiteSpace($Id)) {
    $Id = Get-NextEvidenceId $evidenceDir
}
$idSafe = ($Id -replace '[^\w\-]+', '-').Trim('-')
if ($idSafe -notmatch '^E-') { $idSafe = 'E-' + $idSafe }
$targetFile = Join-Path $evidenceDir ($idSafe + '.md')
if (Test-Path -LiteralPath $targetFile) {
    throw ("证据记录已存在且不可变: {0}。说明: REA 证据一旦写入不改写；请换一个 -Id，并考虑用 supersedes 关联旧记录。" -f $targetFile)
}

if ([string]::IsNullOrWhiteSpace($Title)) {
    $Title = ("REA {0} · {1}" -f $operation, $subjectName)
}
$titleSafe = ConvertTo-SingleLine $Title
$locationValue = if ([string]::IsNullOrWhiteSpace($Location)) { $subjectPath } else { $Location }
$locationSafe = ConvertTo-SingleLine $locationValue

# ------------------------------------------------------------- 6. 写入

$excerptText = $excerpt.ToString().TrimEnd()
$notesText = $notes.ToString().TrimEnd()

if ($DryRun) {
    Write-Step 'DRY-RUN（不写文件）'
    Write-Host ''
    Write-Host ("ID       : {0}" -f $idSafe)
    Write-Host ("Title    : {0}" -f $titleSafe)
    Write-Host ("Severity : {0}   Status: {1}   SourceType: {2}" -f $Severity, $Status, $SourceType)
    Write-Host ("Location : {0}" -f $locationSafe)
    Write-Host ("LinkedWI : {0}" -f $LinkedWorkitem)
    Write-Host ''
    Write-Host '--- repro_command ---'
    Write-Host $repro
    Write-Host '--- raw_excerpt ---'
    Write-Host $excerptText
    Write-Host '--- notes ---'
    Write-Host $notesText
    exit 0
}

$tempFiles = @()
try {
    $excerptPath = [System.IO.Path]::GetTempFileName()
    $notesPath = [System.IO.Path]::GetTempFileName()
    $reproPath = [System.IO.Path]::GetTempFileName()
    $tempFiles += $excerptPath, $notesPath, $reproPath
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($excerptPath, $excerptText, $utf8NoBom)
    [System.IO.File]::WriteAllText($notesPath, $notesText, $utf8NoBom)
    [System.IO.File]::WriteAllText($reproPath, $repro, $utf8NoBom)

    # append-evidence.ps1 以 exit 0 结束，因此必须子进程调用；
    # 使用 -RawExcerptFile / -NotesFile / -ReproCommandFile 规避引号与换行被拆的问题。
    # 注意：-ReproCommand 在 append-evidence 里是 Mandatory，即使给了 -ReproCommandFile
    # 也必须在绑定阶段提供一个占位值，随后被文件内容覆盖。
    $hostExe = (Get-Process -Id $PID).Path
    if ([string]::IsNullOrWhiteSpace($hostExe)) { $hostExe = 'powershell.exe' }
    $arguments = @(
        '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $AppendEvidenceScript,
        '-CaseRoot', $caseFull,
        '-Id', $idSafe,
        '-Title', $titleSafe,
        '-ReproCommand', 'from-ReproCommandFile',
        '-Severity', $Severity,
        '-Status', $Status,
        '-SourceType', $SourceType,
        '-Location', $locationSafe,
        '-RawExcerptFile', $excerptPath,
        '-NotesFile', $notesPath,
        '-ReproCommandFile', $reproPath
    )
    $childOutput = & $hostExe @arguments 2>&1
    $childOutput | ForEach-Object { Write-Host ("  " + $_) }
    if ($LASTEXITCODE -ne 0) {
        throw ("append-evidence.ps1 失败，退出码 {0}" -f $LASTEXITCODE)
    }
} finally {
    foreach ($tempFile in $tempFiles) {
        if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue }
    }
}

if (-not (Test-Path -LiteralPath $targetFile)) {
    throw "写入后未找到证据文件: $targetFile"
}

# ------------------------------- 7. 关联 workitem / timeline（让 review 可追溯）

$linked = (ConvertTo-SingleLine $LinkedWorkitem)
if ($linked -ne 'n/a') {
    if ($linked -notmatch '^WI-\d+') {
        throw ("-LinkedWorkitem 格式应为 WI-xxx，收到: {0}" -f $LinkedWorkitem)
    }

    # 7a. Evidence 侧：记录自身声明的归属
    $content = [System.IO.File]::ReadAllText($targetFile, [System.Text.Encoding]::UTF8)
    $linkedHits = [regex]::Matches($content, '(?m)^-\s*linked_workitem:\s*n/a\s*$')
    if ($linkedHits.Count -ne 1) {
        throw ("期望在 {0} 中找到且仅找到一处 'linked_workitem: n/a'，实际 {1} 处" -f $targetFile, $linkedHits.Count)
    }
    $content = [regex]::Replace($content, '(?m)^-\s*linked_workitem:\s*n/a\s*$', ("- linked_workitem: " + $linked))
    Write-TextPreserving $targetFile $content $false
    Write-Step ("Evidence 已声明归属: {0}" -f $linked)

    # 7b. workitems.md 的 evidence 列：这是 review 判定「已被引用」的依据
    # 仅在 evidence 记录自身写 linked_workitem 时 review 仍报 unlinked，必须落到 workitem 表格。
    $workitemsPath = Join-Path $caseFull 'workitems.md'
    if (-not (Test-Path -LiteralPath $workitemsPath -PathType Leaf)) {
        throw ("找不到 {0}；-LinkedWorkitem 需要 case 的 workitems.md 存在" -f $workitemsPath)
    }
    $workitemsRead = Read-TextPreserving $workitemsPath
    $originalWorkitems = $workitemsRead.Text
    try {
        $lines = $originalWorkitems -split "`r?`n"
        $rowUpdated = $false
        for ($index = 0; $index -lt $lines.Count; $index++) {
            $trimmed = $lines[$index].Trim()
            if (-not ($trimmed.StartsWith('|') -and $trimmed.EndsWith('|'))) { continue }
            $cells = @($trimmed.Substring(1, $trimmed.Length - 2) -split '\|' | ForEach-Object { $_.Trim() })
            if ($cells.Count -lt 8) { continue }
            if ($cells[0] -ne $linked) { continue }
            $existing = @($cells[6] -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
            if ($existing -contains $idSafe) {
                Write-Step ("workitems.md 已包含 {0}，无需修改" -f $idSafe)
                $rowUpdated = $true
                break
            }
            $existing += $idSafe
            $cells[6] = ($existing -join ', ')
            $lines[$index] = '| ' + ($cells -join ' | ') + ' |'
            $rowUpdated = $true
            break
        }
        if (-not $rowUpdated) {
            throw ("workitems.md 中找不到 work item {0}（表格行需为 8 列）" -f $linked)
        }
        $newWorkitems = $lines -join $workitemsRead.Newline
        if ($newWorkitems -ne $originalWorkitems) {
            Write-TextPreserving $workitemsPath $newWorkitems $workitemsRead.Bom
            Write-Step ("workitems.md 的 {0}.evidence 已加入 {1}" -f $linked, $idSafe)
        }
    } catch {
        # 任何一个环节失败都整体回滚，避免把 case 表格改坏
        Write-TextPreserving $workitemsPath $originalWorkitems $workitemsRead.Bom
        throw
    }
}

# 7c. timeline.md 追加一次真实 transition（append-only，不改历史条目）
if (-not $NoTimeline) {
    $timelinePath = Join-Path $caseFull 'timeline.md'
    if (Test-Path -LiteralPath $timelinePath -PathType Leaf) {
        $timelineRead = Read-TextPreserving $timelinePath
        $nl = $timelineRead.Newline
        $timelineBlock = @(
            '',
            ('## {0} | {1} | evidence' -f (Get-Date -Format 'o'), $TimelineRole),
            '- action: rea-evidence-bridge',
            '- command_or_ref: scripts/rea-evidence-bridge.ps1',
            ('- result_summary: REA {0} static analysis recorded as {1}' -f $operation, $idSafe),
            '- artifacts: []',
            ('- evidence_ids: [{0}]' -f $idSafe),
            ('- decision_delta: [evidence_added={0}]' -f $idSafe),
            ('- carry_forward_refs: [scope.md, evidence/{0}.md]' -f $idSafe),
            '- next: triage REA unknowns; promote to Finding only with two or more independent Evidence'
        )
        $newTimeline = $timelineRead.Text.TrimEnd() + $nl + ($timelineBlock -join $nl) + $nl
        Write-TextPreserving $timelinePath $newTimeline $timelineRead.Bom
        Write-Step 'timeline.md 已追加 transition'
    } else {
        Write-Step 'timeline.md 不存在，跳过 timeline 追加'
    }
}

Write-Step ("完成 -> {0}" -f $targetFile)
Write-Host ''
Write-Host '下一步建议：' -ForegroundColor Yellow
Write-Host ("  python `"{0}`" ""{1}"" --verify-hashes --strict" -f (Join-Path (Split-Path -Parent (Split-Path -Parent $AppendEvidenceScript)) 'case-review\scripts\review_case.py'), $caseFull)
exit 0
