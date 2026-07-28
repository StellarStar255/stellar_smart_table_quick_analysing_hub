#!/bin/bash
# Linux deb 打包脚本（在 Linux 上执行）: bash packaging/build_deb.sh
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=$(python3 -c "from version import __version__; print(__version__)")
ARCH=amd64
PKG=smart-table-hub
STAGING=dist/deb-staging
DEB="dist/SmartTableHub-${VERSION}-linux-${ARCH}.deb"

echo "==> PyInstaller 构建 v${VERSION}"
python3 -m PyInstaller packaging/smart_table_hub.spec --noconfirm

echo "==> 组装 deb 目录结构"
rm -rf "$STAGING"
mkdir -p "$STAGING/DEBIAN" \
         "$STAGING/opt/$PKG" \
         "$STAGING/usr/bin" \
         "$STAGING/usr/share/applications" \
         "$STAGING/usr/share/icons/hicolor/512x512/apps"

cp -r dist/SmartTableHub/* "$STAGING/opt/$PKG/"
ln -s "/opt/$PKG/SmartTableHub" "$STAGING/usr/bin/smart-table-hub"

python3 - <<EOF
from PIL import Image
img = Image.open("assets/smart_table_quick_analysing_hub_icon.png")
img.resize((512, 512), Image.LANCZOS).save(
    "$STAGING/usr/share/icons/hicolor/512x512/apps/$PKG.png")
EOF

cat > "$STAGING/usr/share/applications/$PKG.desktop" <<EOF
[Desktop Entry]
Name=Smart Table Hub
Comment=智能表格快速分析工具
Exec=/opt/$PKG/SmartTableHub %f
Icon=$PKG
Terminal=false
Type=Application
Categories=Office;Spreadsheet;
MimeType=application/vnd.openxmlformats-officedocument.spreadsheetml.sheet;application/vnd.ms-excel;text/csv;
EOF

INSTALLED_SIZE=$(du -sk "$STAGING/opt" | cut -f1)
cat > "$STAGING/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: office
Priority: optional
Architecture: $ARCH
Installed-Size: $INSTALLED_SIZE
Maintainer: StellarStar255 <goosehuangmatt@gmail.com>
Depends: libxcb-cursor0 | libxcb-cursor-dev, policykit-1
Description: Smart Table Hub - 智能表格快速分析工具
 基于 PyQt6 的 Excel/CSV 表格查看、编辑与快速分析工具，
 内置公式引擎与 Python 数据分析能力。
EOF

echo "==> 打包 deb"
mkdir -p dist
dpkg-deb --build --root-owner-group "$STAGING" "$DEB"
rm -rf "$STAGING"
echo "==> 完成: $DEB"
