"""quatm -- 独立可安装的实验控制框架核心包。

本包不依赖仓库目录布局：安装（pip install quatm）后可在任意用户项目中
`import quatm`。用户项目（configuration / experiments / Data）通过
`QUATM_PROJECT_ROOT` 环境变量或工作目录定位，见
:mod:`quatm.servers.configreader`。

**首次运行自检**：全新机器上还没有用户项目（没有
``configuration/configfile/configfile.json``）时，任何入口都跑不起来。本包在
导入时做一次自检，如果当前进程是一个 GUI 入口（入口脚本名在
``quatm.GUI.infra.app_meta.APP_META`` 里）且项目不可用，就用子进程拉起
:mod:`quatm.GUI.setup.wizard` 首次运行向导把最小骨架建好，然后继续启动。
不需要自检的场合（测试、脚本化启动）设 ``QUATM_SKIP_FIRST_RUN=1``。
"""

# 版本单一事实源：改这里必须同步 pyproject.toml 的 [project] version。
__version__ = "1.0.1"

import importlib.util
import os
import pkgutil
import sys
import threading

# The public core may be combined with the private platform at build time.
# Extending the package path keeps ``quatm`` importable when both source trees
# are placed on PYTHONPATH without shipping proprietary code in this repository.
__path__ = pkgutil.extend_path(__path__, __name__)


def _maybe_run_first_run_setup():
    """GUI 入口启动时的项目自检（检测不到配置就弹首次运行向导）。

    只 import :mod:`quatm.project_setup`（纯逻辑、不依赖 Qt、不做激活检查）并把
    判断交给它：“当前进程是不是 GUI 入口”“项目在不在”“要不要弹向导”。已经配
    置好的机器上零额外开销、零副作用；非 GUI 进程（服务器/实验子进程）一律不插
    手，由调用方自己报错。

    Returns:
        bool: 可以继续启动为 True；项目不可用且已提示用户为 False。
    """
    if os.environ.get('QUATM_SKIP_FIRST_RUN'):
        return True
    if threading.current_thread() is not threading.main_thread():
        return True
    entry = os.path.basename(sys.argv[0] if sys.argv else '')
    if entry in ('first_run.py', 'project_setup.py'):
        # 向导/命令行工具自己在跑（也避免 `python -m quatm.project_setup` 把本
        # 模块提前 import 进 sys.modules 而触发 runpy 警告）
        return True
    from quatm.project_setup import ensure_project_ready
    return ensure_project_ready(entry=entry)


def _gui_available():
    """Return whether the optional GUI package is installed alongside the core."""
    try:
        return importlib.util.find_spec('quatm.GUI') is not None
    except Exception:  # noqa: BLE001 - a broken optional package is not fatal
        return False


if _gui_available() and not _maybe_run_first_run_setup():
    raise SystemExit(1)


