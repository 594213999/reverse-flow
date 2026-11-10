#Requires -Version 5.1
<#
.SYNOPSIS
    Craft Anything Evidence -> reverse-skill Evidence 桥。

.DESCRIPTION
    把 Craft Anything（craft-anything-cli）导出的 evidence bundle
    （core_evidence_export 产物）转换成 reverse-skill 的
    work/<case>/evidence/E-*.md 记录。

    与 rea-evidence-bridge.ps1 的设计一致：本脚本只做「格式适配」，
    实际写入一律委托官方 skills/scripts/append-evidence.ps1，
    从而自动继承它的不可变写入、字段校验与 INDEX.md 维护行为。

    与 REA 的三个关键差异：
      1. REA 一次输出**一条** Evidence（且可达 200MB 级）；Craft Anything
         一次 export 是一个 bundle，内含**多条**记录 + unknowns。本桥循环写入。
      2. Craft Anything 的 evidence id 是记录内容的 SHA-256，天然幂等：
         同一观察重复导出 id 不变。本桥据此做**去重**——已写入过的记录跳过，
         不触发 append-evidence 的「不可变」拒绝。
      3. Craft Anything 有 clean_room 标记：净室模式下它**没有读过目标内部**，
         只看过访客/用户可见的表面。这直接决定证据能支撑什么结论，
         因此写进 notes 的显著位置。

.PARAMETER CaseRoot
    reverse-skill 的 case 目录（含 evidence/ 子目录），例如 work/craft-ariang。

.PARAMETER BundleJson
    Craft Anything 导出的 bundle 文件。生成方式（本机安装后）：

      craft-anything-cli core-evidence-export --path "C:\abs\out.json"

    或经由 MCP 工具 core_evidence_export（参数 path 必须是绝对路径）。

.PARAMETER OnlyEvidenceId
    只转换指定 id 的记录（逗号分隔，支持前缀匹配）。省略则转换全部。

.PARAMETER MaxRecords
    单次最多写入几条（默认 50）。防止一次 bundle 灌爆 case。

.PARAMETER Severity
    默认 n/a_re（reverse-skill 为纯逆向证据保留的严重度）。

.PARAMETER Status
    默认 observed（静态观察，不是已验证漏洞）。

.PARAMETER SourceType
    默认 file（Craft Anything 证据的对象是产物文件，subject.sha256 是产物摘要）。
    可取值必须是 reverse-skill 枚举：command | screenshot | file | log | memory | network | manual

.PARAMETER LinkedWorkitem
    关联的 work item（WI-xxx）。append-evidence.ps1 不支持该参数，
    因此写入后由本桥做一次精确单行替换并同步 workitems.md 表格。

.PARAMETER DryRun
    只打印将要写入的内容，不落地。

.EXAMPLE
    .\craft-anything-evidence-bridge.ps1 -CaseRoot work\craft-ariang -BundleJson .\ariang.bundle.json -DryRun

.EXAMPLE
    .\craft-anything-evidence-bridge.ps1 -CaseRoot work\craft-ariang -BundleJson .\a.json -LinkedWorkitem WI-002
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $CaseRoot,

    [Parameter(Mandatory = $true)]
    [string] $BundleJson,

    [string] $OnlyEvidenceId = '',
    [int] $MaxRecords = 50,
    [string] $Severity = 'n/a_re',
    [string] $Status = 'observed',
    [string] $SourceType = 'file',
    [string] $LinkedWorkitem = 'n/a',
    [string] $AppendEvidenceScript = '',
    [string] $CliPath = '',
    [int] $MaxBundleBytes = 512 * 1024 * 1024,
    [string] $TimelineRole = 'lead',
    [switch] $NoTimeline,
    [switch] $DryRun
)

$ErrorActionPreference = 'Stop'

function Write-Step([string] $message) {
    Write-Host ("[craft-bridge] {0}" -f $message) -ForegroundColor Cyan
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
    $isStructured = ($value -is [System.Management.Automation.PSCustomObject]) -or
        (($value -is [System.Collections.IEnumerable]) -and (-not ($value -is [string])))
    if ($isStructured) {
        try { return ($value | ConvertTo-Json -Compress -Depth 6) } catch { return [string]$value }
    }
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
if (-not (Test-Path -LiteralPath $BundleJson -PathType Leaf)) {
    throw "BundleJson 不存在: $BundleJson"
}
$bundleFull = (Resolve-Path -LiteralPath $BundleJson).Path
$bundleSize = (Get-Item -LiteralPath $bundleFull).Length
if ($bundleSize -gt $MaxBundleBytes) {
    throw ("bundle {0} 字节，超过上限 {1}。请先用 core_evidence_browse 缩小范围后重新导出。" -f $bundleSize, $MaxBundleBytes)
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

# 定位 craft-anything-cli（用于 repro_command）：-CliPath > $env:CRAFT_ANYTHING_CLI > PATH。
if ([string]::IsNullOrWhiteSpace($CliPath)) {
    if (-not [string]::IsNullOrWhiteSpace($env:CRAFT_ANYTHING_CLI)) {
        $CliPath = $env:CRAFT_ANYTHING_CLI
    } else {
        $onPath = Get-Command 'craft-anything-cli' -ErrorAction SilentlyContinue
        if ($onPath -and $onPath.Source) { $CliPath = $onPath.Source }
    }
}
$cliExe = if ([string]::IsNullOrWhiteSpace($CliPath)) { 'craft-anything-cli' } else { $CliPath }

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

# --------------------------------------------------------- 2. 读取 Craft bundle

Write-Step ("读取 " + (Split-Path -Leaf $bundleFull))
$bundleText = [System.IO.File]::ReadAllText($bundleFull, [System.Text.Encoding]::UTF8)
try {
    $bundle = $bundleText | ConvertFrom-Json
} catch {
    throw ("bundle JSON 解析失败: {0}" -f $_.Exception.Message)
}
if (-not ($bundle.PSObject.Properties.Name -contains 'records')) {
    throw "不是 Craft Anything evidence bundle（缺少 records 字段）"
}
$format = Format-Scalar (Get-DeepValue $bundle 'format')
$version = Format-Scalar (Get-DeepValue $bundle 'version')
$records = @($bundle.records)
$unknownsInBundle = @($bundle.unknowns)
Write-Step ("format={0} version={1} records={2} unknowns={3}" -f $format, $version, $records.Count, $unknownsInBundle.Count)
if ($records.Count -eq 0) {
    Write-Step 'bundle 里没有证据记录，结束'
    exit 0
}

# 过滤
if (-not [string]::IsNullOrWhiteSpace($OnlyEvidenceId)) {
    $wanted = @($OnlyEvidenceId -split ',' | ForEach-Object { $_.Trim() } | Where-Object { $_ -ne '' })
    $records = @($records | Where-Object {
        $rid = [string](Get-DeepValue $_ 'id')
        $hit = $false
        foreach ($w in $wanted) { if ($rid.StartsWith($w)) { $hit = $true; break } }
        $hit
    })
    Write-Step ("按 -OnlyEvidenceId 过滤后剩 {0} 条" -f $records.Count)
    if ($records.Count -eq 0) { Write-Step '没有匹配的记录，结束'; exit 0 }
}
if ($records.Count -gt $MaxRecords) {
    Write-Step ("记录数 {0} 超过 -MaxRecords {1}，只处理前 {1} 条" -f $records.Count, $MaxRecords)
    $records = @($records | Select-Object -First $MaxRecords)
}

# ------------------------------------------------- 3. 幂等：已写入的 craft id

# 扫 evidence/*.md 里的 craft_evidence_id，避免重复写入触发「不可变」拒绝。
$alreadyWritten = @{}
foreach ($file in Get-ChildItem -LiteralPath $evidenceDir -Filter 'E-*.md' -File -ErrorAction SilentlyContinue) {
    $hit = Select-String -LiteralPath $file.FullName -Pattern '^\s*craft_evidence_id:\s*(\S+)\s*$' -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($hit) {
        $value = ([regex]::Match($hit.Line, 'craft_evidence_id:\s*(\S+)')).Groups[1].Value
        if ($value) { $alreadyWritten[$value] = $file.BaseName }
    }
}
Write-Step ("case 里已有的 craft 证据: {0} 条" -f $alreadyWritten.Count)

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

# ------------------------------------------------------- 4. 逐条组装并写入

$written = @()
$skipped = @()
$failed = @()

foreach ($record in $records) {
    $craftId = [string](Get-DeepValue $record 'id')
    if ([string]::IsNullOrWhiteSpace($craftId)) {
        $failed += @{ id = '(no id)'; reason = '记录缺少 id 字段' }
        continue
    }
    if ($alreadyWritten.ContainsKey($craftId)) {
        $skipped += @{ id = $craftId; existing = $alreadyWritten[$craftId] }
        continue
    }

    $operation = Format-Scalar (Get-DeepValue $record 'operation')
    $confidence = Format-Scalar (Get-DeepValue $record 'confidence')
    $authority = Format-Scalar (Get-DeepValue $record 'authority')

    $cleanRoom = Get-DeepValue $record 'environment.clean_room'
    $engine = Format-Scalar (Get-DeepValue $record 'environment.engine')
    $engineVersion = Format-Scalar (Get-DeepValue $record 'environment.engine_version')
    $hostTriple = Format-Scalar (Get-DeepValue $record 'environment.host')
    $languageId = Format-Scalar (Get-DeepValue $record 'environment.language_id')

    $subjectKind = Format-Scalar (Get-DeepValue $record 'subject.kind')
    $subjectSha = Format-Scalar (Get-DeepValue $record 'subject.sha256')
    $subjectName = Format-Scalar (Get-DeepValue $record 'subject.name')

    $coverageStatus = Format-Scalar (Get-DeepValue $record 'coverage.status')
    $coverageExamined = Format-Scalar (Get-DeepValue $record 'coverage.examined')
    $coverageTotal = Format-Scalar (Get-DeepValue $record 'coverage.total')

    $limitations = @(Get-DeepValue $record 'limitations')
    if ($limitations.Count -eq 1 -and $null -eq $limitations[0]) { $limitations = @() }
    $locations = @(Get-DeepValue $record 'locations')
    if ($locations.Count -eq 1 -and $null -eq $locations[0]) { $locations = @() }

    # ---- excerpt
    $excerpt = New-Object System.Text.StringBuilder
    $null = $excerpt.AppendLine('Craft Anything Evidence（结构化摘要；完整记录见 craft_evidence_id）')
    $null = $excerpt.AppendLine("operation: $operation")
    $null = $excerpt.AppendLine("subject: [$subjectKind] $subjectName")
    $null = $excerpt.AppendLine("subject_sha256: $subjectSha")
    $null = $excerpt.AppendLine("confidence/authority: $confidence / $authority")
    $cleanRoomLabel = if ($cleanRoom -eq $true) { 'clean_room=true（未读目标内部，仅外部可见面）' } elseif ($cleanRoom -eq $false) { 'clean_room=false（深度模式，读过目标内部）' } else { 'clean_room=n/a' }
    $null = $excerpt.AppendLine("environment: $cleanRoomLabel; engine=$engine v$engineVersion; host=$hostTriple")
    if ($languageId -ne 'n/a') { $null = $excerpt.AppendLine("language_id: $languageId") }
    $null = $excerpt.AppendLine("coverage: status=$coverageStatus examined=$coverageExamined total=$coverageTotal")

    if ($limitations.Count -gt 0) {
        $null = $excerpt.AppendLine('')
        $null = $excerpt.AppendLine('limitations:')
        foreach ($limitation in $limitations) {
            $code = Format-Scalar (Get-DeepValue $limitation 'code')
            $message = ConvertTo-SingleLine ([string](Get-DeepValue $limitation 'message'))
            $null = $excerpt.AppendLine(("  - [{0}] {1}" -f $code, $message))
        }
    }
    if ($locations.Count -gt 0) {
        $null = $excerpt.AppendLine('')
        $null = $excerpt.AppendLine('locations:')
        $index = 0
        foreach ($location in $locations) {
            if ($index -ge 20) { $null = $excerpt.AppendLine(("  … 其余 {0} 处已省略" -f ($locations.Count - 20))); break }
            $index++
            $kind = Format-Scalar (Get-DeepValue $location 'kind')
            $value = Get-DeepValue $location 'value'
            $where = if ($null -eq $value) { 'n/a' } elseif ($value -is [string]) { $value } else { try { $value | ConvertTo-Json -Compress -Depth 6 } catch { 'n/a' } }
            $null = $excerpt.AppendLine(("  - [{0}] {1}" -f $kind, (ConvertTo-SingleLine ([string]$where))))
        }
    }

    # ---- notes
    $notes = New-Object System.Text.StringBuilder
    $null = $notes.AppendLine('由 craft-anything-evidence-bridge.ps1 从 Craft Anything bundle 转换生成；写入走官方 append-evidence.ps1。')
    $null = $notes.AppendLine('来源身份（可用于复核与去重，勿删）:')
    $null = $notes.AppendLine("  craft_evidence_id: $craftId")
    $null = $notes.AppendLine("  craft_operation: $operation")
    $null = $notes.AppendLine("  subject_sha256: $subjectSha")
    $null = $notes.AppendLine("  engine: $engine v$engineVersion ($hostTriple)")
    $null = $notes.AppendLine("  confidence/authority: $confidence / $authority")
    $null = $notes.AppendLine('')
    if ($cleanRoom -eq $true) {
        $null = $notes.AppendLine('净室语义（clean_room=true）：本次观察没有读取目标内部的代码、文件或页面源码；')
        $null = $notes.AppendLine('结论只建立在外部可见面（截图、可见文本、测得的布局/颜色/字体、窗口画面、录制的运行）。')
        $null = $notes.AppendLine('因此它可以支撑「表面长什么样、行为是什么」，不能单独支撑「内部如何实现」。')
    } elseif ($cleanRoom -eq $false) {
        $null = $notes.AppendLine('深度语义（clean_room=false）：本次观察读过目标内部（代码/文件/运行时）。')
        $null = $notes.AppendLine('结论可支撑内部实现判断，但仍受 coverage 与 limitations 约束。')
    } else {
        $null = $notes.AppendLine('本次记录未标注 clean_room，按普通静态观察对待。')
    }
    $null = $notes.AppendLine('')
    $null = $notes.AppendLine('完整性问题：Craft Anything 的 evidence id 是记录内容的 SHA-256；')
    $null = $notes.AppendLine('记录被改动则 id 不再匹配，core_evidence_exports_compare 会把它标为 tampered。')

    # ---- repro
    $cliId = ($operation -replace '_', '-')
    $repro = @"
# 重放该 Evidence（Craft Anything v0.5 及以后）
& "$cliExe" $cliId --json
# 完整证据再次导出（bundle 便于 diff 与归档）
& "$cliExe" core-evidence-export --path "<绝对路径>.json"
# 若子命令名与上面不符，以「& "$cliExe" commands」为准；本记录的 craft_operation 已原样保存在 notes 中。
"@

    if ($DryRun) {
        Write-Host ''
        Write-Host ("--- DRY-RUN 记录 {0} ---" -f $craftId) -ForegroundColor Yellow
        Write-Host ("operation: {0}  subject: {1}  clean_room: {2}" -f $operation, $subjectName, $cleanRoom)
        Write-Host $excerpt.ToString().TrimEnd()
        Write-Host '--- notes ---'
        Write-Host $notes.ToString().TrimEnd()
        continue
    }

    $idSafe = Get-NextEvidenceId $evidenceDir
    $title = ("Craft Anything {0} · {1}" -f $operation, $subjectName)
    $titleSafe = ConvertTo-SingleLine $title
    $locationSafe = ConvertTo-SingleLine $subjectName

    $tempFiles = @()
    try {
        $excerptPath = [System.IO.Path]::GetTempFileName()
        $notesPath = [System.IO.Path]::GetTempFileName()
        $reproPath = [System.IO.Path]::GetTempFileName()
        $tempFiles += $excerptPath, $notesPath, $reproPath
        $utf8NoBom = New-Object System.Text.UTF8Encoding $false
        [System.IO.File]::WriteAllText($excerptPath, $excerpt.ToString().TrimEnd(), $utf8NoBom)
        [System.IO.File]::WriteAllText($notesPath, $notes.ToString().TrimEnd(), $utf8NoBom)
        [System.IO.File]::WriteAllText($reproPath, $repro, $utf8NoBom)

        # append-evidence.ps1 以 exit 0 结束，因此必须子进程调用；
        # 用 -RawExcerptFile / -NotesFile / -ReproCommandFile 规避引号与换行被拆，
        # 且 -ReproCommand 是 Mandatory，必须给占位值（随后被文件内容覆盖）。
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
    } catch {
        $failed += @{ id = $craftId; reason = $_.Exception.Message }
        continue
    } finally {
        foreach ($tempFile in $tempFiles) {
            if (Test-Path -LiteralPath $tempFile) { Remove-Item -LiteralPath $tempFile -Force -ErrorAction SilentlyContinue }
        }
    }

    $targetFile = Join-Path $evidenceDir ($idSafe + '.md')
    if (-not (Test-Path -LiteralPath $targetFile)) {
        $failed += @{ id = $craftId; reason = ("写入后未找到 {0}" -f $targetFile) }
        continue
    }
    $alreadyWritten[$craftId] = $idSafe
    $written += @{ craft = $craftId; evidence = $idSafe; operation = $operation }
    Write-Step ("写入 {0} <- {1} ({2})" -f $idSafe, $craftId.Substring(0, [Math]::Min(12, $craftId.Length)), $operation)

    # 关联 workitem
    $linked = (ConvertTo-SingleLine $LinkedWorkitem)
    if ($linked -ne 'n/a') {
        if ($linked -notmatch '^WI-\d+') {
            throw ("-LinkedWorkitem 格式应为 WI-xxx，收到: {0}" -f $LinkedWorkitem)
        }
        $content = [System.IO.File]::ReadAllText($targetFile, [System.Text.Encoding]::UTF8)
        $linkedHits = [regex]::Matches($content, '(?m)^-\s*linked_workitem:\s*n/a\s*$')
        if ($linkedHits.Count -eq 1) {
            $content = [regex]::Replace($content, '(?m)^-\s*linked_workitem:\s*n/a\s*$', ("- linked_workitem: " + $linked))
            Write-TextPreserving $targetFile $content $false
        }
        $workitemsPath = Join-Path $caseFull 'workitems.md'
        if (Test-Path -LiteralPath $workitemsPath -PathType Leaf) {
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
                    if ($existing -contains $idSafe) { $rowUpdated = $true; break }
                    $existing += $idSafe
                    $cells[6] = ($existing -join ', ')
                    $lines[$index] = '| ' + ($cells -join ' | ') + ' |'
                    $rowUpdated = $true
                    break
                }
                if ($rowUpdated) {
                    $newWorkitems = $lines -join $workitemsRead.Newline
                    if ($newWorkitems -ne $originalWorkitems) {
                        Write-TextPreserving $workitemsPath $newWorkitems $workitemsRead.Bom
                    }
                }
            } catch {
                Write-TextPreserving $workitemsPath $originalWorkitems $workitemsRead.Bom
                throw
            }
        }
    }
}

# --------------------------------------- 5. timeline：每次运行只追加一个 transition

if (-not $DryRun -and -not $NoTimeline -and $written.Count -gt 0) {
    $timelinePath = Join-Path $caseFull 'timeline.md'
    if (Test-Path -LiteralPath $timelinePath -PathType Leaf) {
        $timelineRead = Read-TextPreserving $timelinePath
        $nl = $timelineRead.Newline
        $ids = @($written | ForEach-Object { $_.evidence })
        $ops = @($written | ForEach-Object { $_.operation } | Select-Object -Unique)
        $timelineBlock = @(
            '',
            ('## {0} | {1} | evidence' -f (Get-Date -Format 'o'), $TimelineRole),
            '- action: craft-anything-evidence-bridge',
            '- command_or_ref: scripts/craft-anything-evidence-bridge.ps1',
            ('- result_summary: Craft Anything static analysis recorded as {0} ({1} records; operations: {2})' -f ($ids -join ', '), $written.Count, ($ops -join ', ')),
            '- artifacts: []',
            ('- evidence_ids: [{0}]' -f ($ids -join ', ')),
            ('- decision_delta: [evidence_added={0}]' -f ($ids -join ',' )),
            '- carry_forward_refs: [scope.md, evidence/]',
            '- next: triage Craft Anything unknowns and limitations; promote to Finding only with two or more independent Evidence'
        )
        $newTimeline = $timelineRead.Text.TrimEnd() + $nl + ($timelineBlock -join $nl) + $nl
        Write-TextPreserving $timelinePath $newTimeline $timelineRead.Bom
        Write-Step 'timeline.md 已追加 transition'
    } else {
        Write-Step 'timeline.md 不存在，跳过 timeline 追加'
    }
}

# ------------------------------------------------------------- 6. 汇总

Write-Host ''
Write-Host '===== 汇总 =====' -ForegroundColor Green
Write-Host ("写入: {0}" -f $written.Count)
foreach ($entry in $written) { Write-Host ("  {0} <- {1} ({2})" -f $entry.evidence, $entry.craft, $entry.operation) }
Write-Host ("跳过（已存在）: {0}" -f $skipped.Count)
foreach ($entry in $skipped) { Write-Host ("  {0} 已在 {1}" -f $entry.id, $entry.existing) }
if ($failed.Count -gt 0) {
    Write-Host ("失败: {0}" -f $failed.Count) -ForegroundColor Red
    foreach ($entry in $failed) { Write-Host ("  {0}: {1}" -f $entry.id, $entry.reason) -ForegroundColor Red }
}

if ($written.Count -gt 0 -and -not $DryRun) {
    Write-Host ''
    Write-Host '下一步建议：' -ForegroundColor Yellow
    Write-Host ("  python `"{0}`" ""{1}"" --verify-hashes --strict" -f (Join-Path (Split-Path -Parent (Split-Path -Parent $AppendEvidenceScript)) 'case-review\scripts\review_case.py'), $caseFull)
}
if ($failed.Count -gt 0) { exit 1 }
exit 0
