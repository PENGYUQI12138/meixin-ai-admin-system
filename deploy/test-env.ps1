param(
    [ValidateSet('start', 'install', 'test', 'configure-browser', 'status', 'stop')]
    [string]$Action = 'status',
    [string]$ScratchDirectory = '',
    [ValidateSet('M1', 'M4')]
    [string]$Generation = 'M1'
)
$ErrorActionPreference = 'Stop'
$AppRoot = Split-Path $PSScriptRoot -Parent
if (-not $ScratchDirectory) {
    $ScratchDirectory = Join-Path (Split-Path (Split-Path $AppRoot -Parent) -Parent) "work/meixin-$($Generation.ToLower())-test"
}
$ScratchDirectory = [IO.Path]::GetFullPath($ScratchDirectory)
if ($ScratchDirectory.StartsWith($AppRoot + [IO.Path]::DirectorySeparatorChar)) {
    throw '测试密码必须放在 App 源码目录以外。'
}
New-Item -ItemType Directory -Force -Path $ScratchDirectory | Out-Null
Set-Content -LiteralPath (Join-Path $ScratchDirectory '.gitignore') -Value '*' -Encoding utf8
foreach ($SecretName in @('db-root-password', 'admin-password')) {
    $SecretPath = Join-Path $ScratchDirectory $SecretName
    if (-not (Test-Path -LiteralPath $SecretPath)) {
        $RandomBytes = New-Object byte[] 32
        $RandomGenerator = [Security.Cryptography.RandomNumberGenerator]::Create()
        $RandomGenerator.GetBytes($RandomBytes)
        $RandomGenerator.Dispose()
        [IO.File]::WriteAllText($SecretPath, [Convert]::ToBase64String($RandomBytes))
    }
}
$env:MEIXIN_TEST_SECRET_DIR = $ScratchDirectory.Replace('\', '/')
$env:MEIXIN_TEST_SITE = "test_meixin_$($Generation.ToLower()).localhost"
$env:MEIXIN_TEST_PORT = if ($Generation -eq 'M4') { '18084' } else { '18081' }
$ComposeArgs = @('compose', '--project-name', "meixin_$($Generation.ToLower())_test", '-f', (Join-Path $PSScriptRoot 'test-compose.yml'))
function Invoke-TestCompose {
    & docker @ComposeArgs @args
    if ($LASTEXITCODE -ne 0) { throw "隔离测试命令失败（退出码 $LASTEXITCODE）。" }
}
switch ($Action) {
    'start' {
        Invoke-TestCompose up -d
        Invoke-TestCompose exec -T app env/bin/python /opt/meixin-test/bootstrap.py bootstrap
    }
    'install' {
        Invoke-TestCompose exec -T app env/bin/python /opt/meixin-test/bootstrap.py install
        Invoke-TestCompose restart app
    }
    'test' { Invoke-TestCompose exec -T app env/bin/python /opt/meixin-test/bootstrap.py test }
    'configure-browser' { Invoke-TestCompose exec -T app env/bin/python /opt/meixin-test/bootstrap.py configure-browser }
    'status' { Invoke-TestCompose ps }
    'stop' { Invoke-TestCompose stop }
}
