# DEB 打包与安装说明

本文档描述 `video-subtitle-ocr` 的 Debian 软件包结构、构建流程、维护脚本设计，
以及无 root 权限下的安装测试方法。

## 目录

- [目录总览](#目录总览)
- [包内容布局](#包内容布局)
- [设计决策](#设计决策)
- [维护脚本详解](#维护脚本详解)
- [构建流程（build_deb.sh）](#构建流程build_debsh)
- [安装测试（无 root 环境）](#安装测试无-root-环境)
- [版本发布检查清单](#版本发布检查清单)
- [常见问题](#常见问题)

## 目录总览

仓库分为两个顶层部分：

```
video_subtitle_ocr/            # 应用源码（内含自己的 .git）
video-subtitle-ocr/            # deb 打包树（由 build_deb.sh 维护）
├── DEBIAN/
│   ├── control                # 包元数据（名称/版本/依赖/描述）
│   ├── postinst               # 安装后配置：创建 venv 并安装 Python 依赖
│   ├── prerm                  # 卸载前钩子（当前为空操作占位）
│   └── postrm                 # 卸载后清理：purge 时删除 venv 与残留
├── opt/apps/video_subtitle_ocr/   # 应用文件（构建时由源码目录 rsync 同步）
└── usr/
    ├── bin/
    │   ├── video-subtitle-ocr       # GUI 启动器
    │   └── video-subtitle-ocr-cli   # CLI 启动器
    └── share/
        ├── applications/video_subtitle_ocr.desktop
        ├── doc/video-subtitle-ocr/copyright
        ├── icons/hicolor/256x256/apps/video-subtitle-ocr.png
        └── man/man1/*.1             # man 手册页（构建时 gzip）
```

## 包内容布局

| 安装路径 | 内容 |
| --- | --- |
| `/opt/apps/video_subtitle_ocr/` | 全部应用源码（`main.py`、`cli.py`、`core/`、`components/`、`utils/`、`i18n/`、`resources/`、`docs/`、`benchmarks/` 等） |
| `/opt/apps/video_subtitle_ocr/.venv/` | postinst 创建的 Python 虚拟环境（非包内文件） |
| `/usr/bin/video-subtitle-ocr` | GUI 启动器（bash） |
| `/usr/bin/video-subtitle-ocr-cli` | CLI 启动器（bash） |
| `/usr/share/applications/video_subtitle_ocr.desktop` | 应用菜单入口 |
| `/usr/share/icons/hicolor/256x256/apps/video-subtitle-ocr.png` | 应用图标 |
| `/usr/share/man/man1/video-subtitle-ocr.1.gz` | GUI man 手册 |
| `/usr/share/man/man1/video-subtitle-ocr-cli.1.gz` | CLI man 手册 |
| `/usr/share/doc/video-subtitle-ocr/copyright` | Debian 格式版权文件（Apache-2.0 全文） |

打包时会排除开发产物：`.git/`、`.venv/`、`__pycache__/`、`*.pyc`、
`.pytest_cache/`、`tests/`、`requirements-dev.txt`、`requirements-a.txt`、
`requirements-full-pinned.txt`、`.gitignore`。

## 设计决策

1. **为什么用 pip + venv 而不是 python3-* 系统包依赖？**
   PaddlePaddle / PaddleOCR / RapidOCR 等 OCR 运行时没有可靠的 apt 包源，
   PySide6 的 apt 版本也往往滞后。因此包内只声明最小系统依赖
   （`python3`、`python3-venv`、`python3-pip`、`ca-certificates`），
   由 postinst 在 `/opt/apps/video_subtitle_ocr/.venv` 中创建独立虚拟环境并
   安装 `requirements.txt`。这与 `/opt/apps` 惯例（deepin/uos 风格）一致。

2. **依赖安装的有界退避（bounded backoff）**
   postinst 中的 pip 安装最多重试 5 次，等待序列 5/10/20/40/60 秒
   （单次等待上限 60 秒，总等待上限 135 秒）。重试耗尽后 postinst 以非零
   状态退出并打印修复指引（`sudo dpkg --configure -a` 或手动 pip），
   不会无限阻塞，也不会静默留下损坏的安装——之后网络恢复时执行一次
   `dpkg --configure -a` 即可续装（postinst 幂等）。

3. **`remove` 保留 venv，`purge` 才删除**
   venv 约 2 GB+，由 postinst 生成、不属于包文件清单。`dpkg -r` 时 postrm
   不做清理，重装/升级无需重新下载数百 MB 的 wheel；`dpkg -P`（purge）时
   `rm -rf /opt/apps/video_subtitle_ocr` 一并清除。

4. **维护脚本支持 `DPKG_ROOT`**
   postinst/postrm 均以 `ROOT="${DPKG_ROOT:-}"` 拼接路径，符合 dpkg ≥1.20
   的惯例，使 `dpkg --root=DIR --force-not-root --force-script-chrootless`
   的无 root 安装测试成为可能（见下文）。

5. **启动器支持 `VSO_APP_DIR` 覆盖**
   两个启动器默认使用 `/opt/apps/video_subtitle_ocr`，可用环境变量
   `VSO_APP_DIR` 指向其他位置（staging/测试/便携部署），正常安装不受影响。
   venv 缺失时启动器输出可操作的修复指引并以状态码 1 退出。

6. **Architecture: all**
   应用为纯 Python，无架构相关二进制；CPU/GPU 差异由运行期依赖
   （`requirements.txt` vs `requirements-gpu.txt`）解决，包本身通用。

## 维护脚本详解

### postinst（configure 阶段）

```
1. 校验参数；venv 不存在时 python3 -m venv 创建
2. 升级 venv 内 pip（失败容忍，best-effort）
3. 有界退避重试安装 requirements.txt（见设计决策 2）
4. 失败 → 打印修复指引，exit 1（dpkg 标记为未配置，可恢复）
5. 成功 → update-desktop-database / gtk-update-icon-cache（best-effort）
```

### prerm

当前无需停止任何服务，保留为占位钩子。

### postrm

```
remove:  保留 venv（快速重装）
purge:   rm -rf /opt/apps/video_subtitle_ocr，尝试 rmdir /opt/apps
```

## 构建流程（build_deb.sh）

在仓库根目录执行 `./build_deb.sh`，步骤：

1. **环境检测**：OS/内核/架构，校验 `dpkg-deb` 等构建工具。
2. **源码同步**：`rsync -a --delete --delete-excluded` 将
   `video_subtitle_ocr/` 同步到 `video-subtitle-ocr/opt/apps/video_subtitle_ocr/`
   （排除清单见上）；无 rsync 时回退到 tar 管道方案。
3. **文件校验**：control、postinst/prerm/postrm、两个启动器、`main.py`、
   `cli.py`、两份 requirements、`preload_models.py` 必须存在；校验
   requirements-gpu.txt 含 `paddlepaddle-gpu`。
4. **元数据读取**：从 control 提取包名/版本/架构，校验与构建机架构兼容（`all` 直接放行）。
5. **清理**：删除 `__pycache__`、`*.pyc`、编辑器临时文件、残留 `.venv`/`.gpu_mode`/`.paddleocr`。
6. **权限**：DEBIAN 脚本 0755、control 0644、启动器 0755、`*.py` 0644。
7. **man 页压缩**：`gzip -9n` 所有 `.1`。
8. **Installed-Size**：`du -sk` 自动计算并写回 control。
9. **构建**：`dpkg-deb --root-owner-group --build`，输出
   `video-subtitle-ocr_<版本>_<架构>.deb`。

## 安装测试（无 root 环境）

无 sudo 权限时，可用 dpkg 的用户态模式在 staging 根中完整模拟安装，
覆盖 postinst（含真实 pip 安装）、remove、purge 全流程：

```bash
# 1. 初始化 staging 根（dpkg 数据库骨架）
rm -rf /tmp/instroot
mkdir -p /tmp/instroot/var/lib/dpkg/{updates,info,triggers,alternatives}
mkdir -p /tmp/instroot/{var/log,etc,opt,usr}
: > /tmp/instroot/var/lib/dpkg/status

# 2. 安装（解包 → 更新数据库 → 以 DPKG_ROOT 运行 postinst）
dpkg --root=/tmp/instroot --force-not-root --force-script-chrootless \
     --force-depends -i video-subtitle-ocr_*_all.deb

# 3. 功能验证（启动器支持 VSO_APP_DIR 指向 staging 根）
export VSO_APP_DIR=/tmp/instroot/opt/apps/video_subtitle_ocr
/tmp/instroot/usr/bin/video-subtitle-ocr-cli --version
/tmp/instroot/usr/bin/video-subtitle-ocr-cli 某视频.mp4 -o /tmp/out.ass

# GUI 冒烟（虚拟显示）
xvfb-run -a env QT_QPA_PLATFORM=offscreen "$VSO_APP_DIR/.venv/bin/python" -c "
import sys; sys.path.insert(0, '$VSO_APP_DIR')
from PySide6.QtWidgets import QApplication
from main_window import SubtitleOCRGUI
app = QApplication([]); w = SubtitleOCRGUI(); w.show(); print('GUI OK', w.windowTitle())"

# 4. 卸载验证
dpkg --root=/tmp/instroot --force-not-root --force-script-chrootless -r video-subtitle-ocr
#   remove 后：/usr/bin 启动器已删，.venv 保留
dpkg --root=/tmp/instroot --force-not-root --force-script-chrootless -P video-subtitle-ocr
#   purge 后：/opt/apps/video_subtitle_ocr（含 venv）全部清除，数据库无记录
```

说明：

- `--force-depends` 跳过依赖检查——staging 根的 dpkg 数据库是空的，
  系统依赖实际由宿主机满足。
- `--force-not-root --force-script-chrootless` 使维护脚本在宿主环境运行，
  并通过 `DPKG_ROOT` 环境变量获知目标根前缀。
- 真实系统的安装仍是标准方式：`sudo dpkg -i *.deb`。

## 版本发布检查清单

1. 更新 `video-subtitle-ocr/DEBIAN/control` 的 `Version:`。
2. 同步更新 `video_subtitle_ocr/cli.py` 中的 `__version__`。
3. 如界面文案有增删，更新翻译：在 `video_subtitle_ocr/` 目录下运行

   ```bash
   # 刷新 .ts（-tr-function-alias 使 components/scan_review_dialog.py 的
   # _tr() 包装也能被识别；勿省略，否则对应条目会被误标为 vanished）
   .venv/bin/pyside6-lupdate -tr-function-alias translate+=_tr \
       main.py cli.py preload_models.py main_window/*.py components/*.py \
       core/*.py core/subtitle_generator/*.py utils/*.py i18n/translator.py \
       -ts i18n/app_ja_JP.ts i18n/app_zh_CN.ts
   # 补译后编译
   .venv/bin/pyside6-lrelease i18n/app_ja_JP.ts i18n/app_ja_JP.qm
   .venv/bin/pyside6-lrelease i18n/app_zh_CN.ts i18n/app_zh_CN.qm
   ```

4. 如有新文件/目录，确认 rsync 排除清单仍然正确。
5. 如启动参数或用法变化，更新两份 man 页与 `usr/share/applications/*.desktop`。
6. `./build_deb.sh` 重新构建，`dpkg-deb -I/-c` 抽查。
7. 按“安装测试（无 root 环境）”章节跑一遍安装/CLI/GUI/卸载。
8. 更新 README 基准数据（如模型或流水线有变化），并在
   [testing.md](testing.md) 追加本版测试记录。

## 常见问题

**安装时卡在 pip 下载？**
首次安装需联网下载约数百 MB wheel。网络不稳时 postinst 会自动退避重试；
彻底失败后 `sudo dpkg --configure -a` 可在恢复后续装。

**GUI 无法启动，报 Qt 平台插件错误？**
安装 Recommends 中的运行库：`sudo apt install libgl1 libegl1 libxkbcommon0
libdbus-1-3 libfontconfig1 libglib2.0-0`。无显示器的机器请用
`video-subtitle-ocr-cli`。

**GPU 模式如何启用？**
`sudo /opt/apps/video_subtitle_ocr/.venv/bin/pip install -r
/opt/apps/video_subtitle_ocr/requirements-gpu.txt`（需 NVIDIA 驱动 + CUDA 12.x），
并在 `/opt/apps/video_subtitle_ocr/.gpu_mode` 中写入 `export MODE="gpu"`。
