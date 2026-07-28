# Windows exe 安装包构建脚本（在 Windows 上执行）:
#   powershell -ExecutionPolicy Bypass -File packaging\build_exe.ps1
#
# 代码签名（可选）: 设置环境变量后自动用 signtool 签名
#   $env:WIN_CERT_PFX_PATH = "C:\path\cert.pfx"
#   $env:WIN_CERT_PASSWORD = "..."
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

$Version = python -c "from version import __version__; print(__version__)"
Write-Host "==> PyInstaller build v$Version"
python -m PyInstaller packaging/smart_table_hub.spec --noconfirm

# 可选：对主程序 exe 签名
if ($env:WIN_CERT_PFX_PATH) {
    Write-Host "==> Signing SmartTableHub.exe"
    & signtool sign /f $env:WIN_CERT_PFX_PATH /p $env:WIN_CERT_PASSWORD `
        /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
        "dist\SmartTableHub\SmartTableHub.exe"
}

Write-Host "==> Building installer (Inno Setup)"
$iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $iscc)) { $iscc = "ISCC.exe" }
& $iscc "/DAppVersion=$Version" "packaging\installer.iss"

$Installer = "dist\SmartTableHub-$Version-windows-x64-setup.exe"
if ($env:WIN_CERT_PFX_PATH) {
    Write-Host "==> Signing installer"
    & signtool sign /f $env:WIN_CERT_PFX_PATH /p $env:WIN_CERT_PASSWORD `
        /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 $Installer
}
Write-Host "==> Done: $Installer"
