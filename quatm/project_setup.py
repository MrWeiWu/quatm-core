"""首次运行的项目初始化（first-run project setup）。

**为什么需要它**：quatm 把“程序本体”（本包 ``quatm/``）与“用户项目”
（``configuration`` / ``experiments`` / ``Data`` / ``images``）分开存放。全新机器
上项目目录还不存在时，``configuration/configfile/configfile.json`` 打不开，
``Config()`` 直接抛 ``FileNotFoundError``，于是**任何入口都跑不起来**。本模块
负责“项目自检 + 生成最小可用骨架”，并且刻意做到：

* **不 import Qt**：没有显示器的机器（服务器、CI、自动化部署）也能用；
* **不触发激活检查**：本模块只 import 标准库与 ``quatm.GUI.infra.app_meta``
  （纯数据模块），全新机器上还没激活也能先把项目建好；
* **绝不覆盖已有文件**：只创建缺失项，已存在的一律保留并逐项报告。

三处复用本模块：

1. GUI 首次运行向导 :mod:`quatm.GUI.setup.wizard`（检测不到配置时自动弹出）；
2. 命令行：``python -m quatm.project_setup --check`` / ``--create``
   （无显示器机器与自动化部署）；
3. 测试。

生成的项目骨架（与仓库里现有的 ``configuration/`` 完全同构）：

    <root>/configuration/configfile/configfile.json   服务/GUI 条目与端点
    <root>/configuration/properties/properties.json   属性盘（启动时为空表）
    <root>/experiments/                               实验脚本目录
    <root>/experiments/README.md                      放什么脚本的说明
    <root>/Data/                                      测量数据
    <root>/images/                                    图像
    <root>/.quatm_project                             项目标记（辅助定位项目根）

程序包**不复制**进项目目录：``configfile`` 里的 ``script`` 用
``../quatm/GUI/entries/xxx.py`` 这种相对写法，解析时会自动落到已安装的包模块上
（见 :func:`quatm.GUI.entries.processmanager.resolve_script`），因此同一份配置
既能用于“仓库里就地运行”，也能用于“pip 安装后运行”。
"""

import argparse
import copy
import datetime
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile


def _load_app_meta():
    """按**文件路径**加载身份表 ``quatm/GUI/infra/app_meta.py``。

    不能写成 ``from quatm.GUI.infra.app_meta import APP_META``：那会先执行
    ``quatm/GUI/__init__.py``（High-DPI/Qt 主题）与
    ``quatm/GUI/infra/__init__.py``（i18n → ``quatm.utils`` → **激活检查**），
    于是“未激活的新机器上先用本模块建好项目”就失效了。
    ``bin/install_quatm_launchers.py`` 出于同样原因也是按路径加载它的。

    Returns:
        dict: ``脚本名 -> (显示名, desktop id, 图标文件名)``；读不到时返回空字典。
    """
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        'GUI', 'infra', 'app_meta.py')
    try:
        spec = importlib.util.spec_from_file_location('_quatm_app_meta', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return dict(module.APP_META)
    except Exception:  # noqa: BLE001 - 身份表只是“顺手多登记几个程序”
        return {}


# 语言包实现在 :mod:`quatm.i18n`（只依赖标准库，不做激活检查），直接用即可：
# 命令行与向导的说明文字都能翻译，而不会在未激活的机器上被拦下。
from quatm.i18n import tr  # noqa: E402


_APP_META_CACHE = None


def app_meta():
    """返回身份表（``脚本名 -> (显示名, desktop id, 图标文件名)``，带缓存）。

    Returns:
        dict: 身份表；读不到时为空字典。
    """
    global _APP_META_CACHE
    if _APP_META_CACHE is None:
        _APP_META_CACHE = _load_app_meta()
    return _APP_META_CACHE

# ---------------------------------------------------------------------------
# 常量：项目骨架与端点
# ---------------------------------------------------------------------------
CONFIGFILE_REL = os.path.join('configuration', 'configfile', 'configfile.json')
PROPERTIES_REL = os.path.join('configuration', 'properties', 'properties.json')
MARKER_REL = '.quatm_project'
EXPERIMENTS_README_REL = os.path.join('experiments', 'README.md')

#: 需要创建的项目目录（相对项目根）。
PROJECT_DIRS = ('configuration/configfile', 'configuration/properties',
                'experiments', 'Data', 'images')

#: 项目根提示文件（与 :mod:`quatm.servers.configreader`、
#: :mod:`quatm.licensing.tool` 读同一个文件：让“在任何目录启动”都能找到项目）。
PROJECT_ROOT_HINT = os.path.expanduser('~/.quatm_project_root')

#: 五个消息/数据枢纽：**没有它们框架跑不起来**（Properties/消息/数据/命令/图像）。
HUBS = (
    ('Image Hub', 'imagepub', 'imagesub'),
    ('Command Hub', 'controlpub', 'controlsub'),
    ('Data Hub', 'datapub', 'datasub'),
    ('Property Hub', 'propertypub', 'propertysub'),
    ('Message Hub', 'messagepub', 'messagesub'),
)

#: 四个记录器：把属性/数据/图像/消息落盘（缺了仍能跑，但数据不留档）。
LOGGERS = (
    ('Property Logger', 'propertylogger.py', 'propertyrep'),
    ('Data Logger', 'datalogger.py', None),
    ('Image Logger', 'imagelogger.py', None),
    ('Message Logger', 'messagelogger.py', None),
)

#: 判定“这台机器能不能跑”的必需服务（缺任一 → 配置不可用）。
REQUIRED_SERVERS = tuple(name for name, _pub, _sub in HUBS)

#: 五个功能分类（与 ProcessManager / web 进程服务的分类一致）。
CATEGORIES = ('Servers', 'Driver', 'GUI', 'Viewer', 'Monitor')

#: 模板里各程序所属分类（参考仓库现有 configfile；不在表里的 APP_META 条目
#: 一律放进 ``GUI`` 并默认不启用，方便用户在 ProcessManager 里按需勾选）。
GUI_ORDER = (
    ('property_editor.py', 'Property Manager'),
    ('browser.py', 'Experiment Browser'),
    ('analysismanager.py', 'Analysis Manager'),
    ('h5storage.py', 'Data Manager'),
    ('dashboard.py', 'Dashboard'),
    ('streammonitor.py', 'Stream Monitor'),
    ('little_helper.py', 'Experiment Helper'),
    ('experiment_timeline.py', 'Experiment Timeline'),
)
VIEWER_ORDER = (
    ('image_group.py', 'Camera Viewer'),
    ('live_plot.py', 'Live Plot'),
)

#: 默认勾选（新机器上先跑得起来的最小集合：实验浏览器）。
DEFAULT_ACTIVE = ('browser.py',)

#: 首次运行向导里默认勾选的程序。比 :data:`DEFAULT_ACTIVE` 稍宽一点：实验浏览器是
#: 主工作窗口，数据流监控用来盯服务；其余按需勾。
WIZARD_ACTIVE = ('browser.py', 'streammonitor.py')

_EXPERIMENTS_README = """\
# Experiments

Put the experiment scripts (`.py`) of this machine in this directory.
The file list on the left of the Browser shows exactly this folder; double-click a
file to open it as an experiment window.

把本机的实验脚本（`.py`）放在这个目录里。Browser 左侧的文件列表显示的就是它，
双击文件即可作为实验窗口打开。
"""


#: APP_META 里这些条目只用于“运行期身份识别”，**不写进新项目 configfile**：
#: ``license_manager_admin`` 是内部签发工具，客户项目模板不该出现；
#: ``simple_defaults`` 是 dashboard 的旧入口名（同一窗口），不重复登记。
_TEMPLATE_EXCLUDED = {'simple_defaults.py', 'license_manager_admin.py'}


def program_entries(category):
    """列出某个分类下要写进 configfile 的程序条目（保持稳定顺序）。

    ``GUI`` 除模板里的八个程序外，还会把 ``APP_META`` 里其余程序一并登记
    （默认不启用）：新机器的 ProcessManager 因此能看到“这台机器能跑哪些程序”，
    勾一下即可启用。``Viewer`` 只有模板里那两个（图像查看、实时曲线）；
    ``Driver`` / ``Monitor`` 的分类属于**每台机器各不相同**的硬件脚本，模板里
    留空，由用户在 ProcessManager 里自行登记。

    Args:
        category (str): ``'GUI'`` 或 ``'Viewer'``。

    Returns:
        list[tuple[str, str]]: ``(脚本文件名, 配置里的条目名)``。
    """
    if category != 'GUI':
        return list(VIEWER_ORDER)
    base = list(GUI_ORDER)
    known = {script for script, _name in base}
    for script, (name, _desktop_id, _icon) in app_meta().items():
        if script in known or script in _TEMPLATE_EXCLUDED:
            continue
        base.append((script, name))
    return base


def _entry_script_value(script):
    """把向导里选的“程序”写成 configfile 的 ``script`` 值。

    两种写法：裸脚本名（``browser.py``）→ ``../quatm/GUI/entries/browser.py``；
    已是路径（``monitor/my_monitor.py`` / 用户项目内相对路径）→ 原样保留，
    ``resolve_script`` 会按项目内文件解析。

    Args:
        script (str): 脚本名或项目内相对路径。

    Returns:
        str: 写进配置的 script 值。
    """
    text = str(script or '').strip().replace('\\', '/')
    if '/' not in text:
        return _script_value(text)
    return text


def _service_enabled(services, name):
    """服务是否启用（``services`` 为 None 时全部启用，未列的键默认启用）。

    Args:
        services (Mapping[str, bool]|None): 用户勾选结果。
        name (str): 服务名。

    Returns:
        bool: 启用为 True。
    """
    if not services:
        return True
    return bool(services.get(name, True))


def _named_entry(item):
    """拆出 ``{'name', 'script'}`` 条目（缺名时用文件名当名字）。

    Args:
        item (dict): 条目。

    Returns:
        tuple[str, str]: ``(名字, script 值)``；不完整时返回 ``('', '')``。
    """
    if not isinstance(item, dict):
        return '', ''
    script = str(item.get('script') or '').strip()
    name = str(item.get('name') or '').strip()
    if not name and script:
        name = os.path.splitext(os.path.basename(script))[0]
    if not (name and script):
        return '', ''
    return name, _entry_script_value(script)


def _script_value(script):
    """把入口脚本名写成 configfile 里的 ``script`` 值（相对包路径写法）。

    Args:
        script (str): ``quatm/GUI/entries`` 下的脚本文件名。

    Returns:
        str: 形如 ``'../quatm/GUI/entries/browser.py'`` 的路径。
    """
    return ('../quatm/GUI/entries/%s' % script).replace('\\', '/')


def default_configfile(gui_active=DEFAULT_ACTIVE, extra_active=(), services=None,
                      instances=(), drivers=(), monitors=(),
                      only_selected=False):
    """生成一份最小可用的 configfile 内容。

    结构参照仓库现有的 ``configuration/configfile/configfile.json``：五个枢纽
    用本机 ``ipc:///tmp/<名>`` 端点（Windows 上 :func:`quatm.servers.configreader.Config`
    会自动改写为仅回环的 tcp），四个记录器写属性/数据/图像/消息。

    Args:
        gui_active (Iterable[str]): 默认启用的 GUI/Viewer 脚本名。
        extra_active (Iterable[str]): 额外启用的分类条目名。
        services (Mapping[str, bool]|None): 服务的启用开关（键为服务名，见
            :data:`HUBS` / :data:`LOGGERS`）；None = 全部启用。
        instances (Iterable[dict]): 额外程序实例，每项
            ``{'category', 'name', 'script', 'active'}``。``script`` 既可以是
            ``quatm/GUI/entries`` 下的脚本名，也可以是项目内的相对路径
            （如 ``monitor/my_monitor.py``）。用于「同一个程序开多个、各自命名」
            （例如三个不同相机的 Image Monitor）。
        drivers (Iterable[dict]): 驱动条目，每项 ``{'name', 'script'}``，写进
            ``Driver`` 分类。
        monitors (Iterable[dict]): 监视器条目，每项 ``{'name', 'script'}``，写进
            ``Monitor`` 分类。
        only_selected (bool): True 时 GUI/Viewer 段**只写勾选的程序**（首次运行
            向导用：向导里勾了哪些窗口，进程管理器里就只出现哪些，不再摆出一屏
            用不到的灰色面板）；False（默认）是历史行为：全部程序都登记，只把勾
            选的标成 ``active``（“先登记、要用再启动”）。勾选为空时一律退回后者
            —— ``GUI`` 段不能空，否则 :func:`inspect_project` 会判 incomplete，
            每次启动管理器都弹向导。

    Returns:
        dict: configfile 字典（可直接 json.dump）。
    """
    active = set(gui_active or ()) | set(extra_active or ())
    conf = {}
    for category in ('GUI', 'Viewer'):
        section = {}
        for script, name in program_entries(category):
            if only_selected and active and script not in active:
                continue
            section[name] = {'active': script in active,
                             'script': _script_value(script)}
        if category == 'GUI' and not section:
            # 一个 GUI 都没轮到（用户什么都没勾）：退回“全登记、都不自动启动”
            section = {name: {'active': False, 'script': _script_value(script)}
                       for script, name in program_entries('GUI')}
        conf[category] = section
    conf['Driver'] = {}
    conf['Monitor'] = {}
    for item in instances or ():
        category = str(item.get('category') or 'GUI')
        name = str(item.get('name') or '').strip()
        script = str(item.get('script') or '').strip()
        if not (name and script):
            continue
        conf.setdefault(category, {})[name] = {
            'active': bool(item.get('active', True)),
            'script': _entry_script_value(script)}
    for item in drivers or ():
        name, script = _named_entry(item)
        if name:
            conf['Driver'][name] = {'active': True, 'script': script}
    for item in monitors or ():
        name, script = _named_entry(item)
        if name:
            conf['Monitor'][name] = {'active': True, 'script': script}
    conf['files'] = {'startup_properties': ['properties.json'],
                     'startup_preferences': ['peferences.json']}
    servers = {}
    for name, pub, sub in HUBS:
        servers[name] = {'active': _service_enabled(services, name),
                         'pub': 'ipc:///tmp/%s' % pub,
                         'sub': 'ipc:///tmp/%s' % sub,
                         'script': '../quatm/servers/xsub_xpub.py'}
    for name, script, rep in LOGGERS:
        entry = {'active': _service_enabled(services, name),
                 'script': '../quatm/servers/%s' % script}
        if rep:
            entry['rep'] = 'ipc:///tmp/%s' % rep
        servers[name] = entry
    # 推送网关：可选服务（不是必备项，缺了不影响实验），默认不随项目启动。
    # 它把 Message Hub 上的 warning/error 转成手机推送，配置见
    # configuration/notifications.json 的 gateway 段。
    servers['Push Gateway'] = {'active': False,
                               'script': '../quatm/servers/pushgateway.py'}
    conf['Servers'] = servers
    return conf


def validate_config(config, translate=False):
    """检查 configfile 是否“这台机器能跑”。

    Args:
        config (dict): configfile 内容。
        translate (bool): True 时把问题句子翻成当前语言。**默认 False**：本函数
            也在首次运行向导 / 激活向导的入口自检路径上被调用，那时还没确定用
            哪个项目的语言包，也没必要为此初始化 i18n；自检只求“能跑/不能跑”。

    Returns:
        list[str]: 问题清单；没有问题时为空列表。
    """
    t = tr if translate else (lambda text: text)
    problems = []
    if not isinstance(config, dict):
        return [t('The configuration file is not a JSON object.')]
    servers = config.get('Servers')
    if not isinstance(servers, dict) or not servers:
        problems.append(t('The configuration has no "Servers" section.'))
        return problems
    compact = {str(k).replace(' ', '').lower() for k in servers}
    for name in REQUIRED_SERVERS:
        if name.replace(' ', '').lower() not in compact:
            problems.append(t('Missing server: %s') % name)
    for name, entry in servers.items():
        if not isinstance(entry, dict):
            problems.append(t('Server "%s" is not an object.') % name)
            continue
        if not entry.get('script'):
            problems.append(t('Server "%s" has no script.') % name)
        # 枢纽必须有 pub/sub；记录器没有端点也合法。
        if name in REQUIRED_SERVERS and not (entry.get('pub') and entry.get('sub')):
            problems.append(t('Server "%s" must define pub and sub endpoints.')
                            % name)
    for category in ('GUI', 'Viewer'):
        section = config.get(category, {})
        if not isinstance(section, dict):
            problems.append(t('Section "%s" is not an object.') % category)
            continue
        for name, entry in section.items():
            if not isinstance(entry, dict) or not entry.get('script'):
                problems.append(t('Entry "%s/%s" has no script.')
                                % (category, name))
    return problems

def _read_json(path):
    """读取 JSON 文件。

    Args:
        path (str): 文件路径。

    Returns:
        dict|None: 解析结果；文件不存在或格式错误时返回 None。
    """
    try:
        with open(path, encoding='utf-8') as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def find_project_root(start=None):
    """定位当前项目根（与 :mod:`quatm.servers.configreader` 同一套规则）。

    这里**不 import configreader**（它会触发激活检查），只复制那套“从 cwd 向上
    找标记”的规则；两者都读同一个环境变量、同一个项目标记与同一个项目根提示
    文件（:data:`PROJECT_ROOT_HINT`），因此“该用哪个项目”两边一致。

    Args:
        start (str|None): 起始目录（默认当前工作目录）。

    Returns:
        str: 项目根绝对路径；找不到标记时返回起始目录（由调用方决定怎么办）。
    """
    env = os.environ.get('QUATM_PROJECT_ROOT', '').strip()
    if env:
        return os.path.abspath(env)
    base = os.path.abspath(start or os.getcwd())
    current = base
    while True:
        if os.path.isfile(os.path.join(current, CONFIGFILE_REL)):
            return current
        if os.path.isfile(os.path.join(current, MARKER_REL)):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    remembered = _read_project_root_hint()
    if remembered and os.path.isfile(os.path.join(remembered, CONFIGFILE_REL)):
        return remembered
    return base


def inspect_project(root, translate=False):
    """自检一个项目目录：配置在不在、能不能用、缺什么。

    Args:
        root (str): 项目根目录。
        translate (bool): 是否把问题句子翻成当前语言（见 :func:`validate_config`；
            入口自检路径必须保持 False）。

    Returns:
        dict: ``{'root', 'state', 'configfile', 'config', 'problems',
            'missing'}``。``state`` 取值：

            * ``'ok'``         配置文件存在、能解析、必需服务齐全；
            * ``'incomplete'`` 能跑，但缺记录器/GUI 段（只在需要这些段的入口
              才提示，避免每次启动都唠叨）；
            * ``'broken'``     文件存在但解析失败或缺少必需服务（跑不起来）；
            * ``'missing'``    完全没有配置文件（全新机器）。
    """
    root = os.path.abspath(str(root))
    path = os.path.join(root, CONFIGFILE_REL)
    missing = []
    if not os.path.isfile(path):
        missing.append(CONFIGFILE_REL)
    for rel in PROJECT_DIRS:
        if not os.path.isdir(os.path.join(root, rel)):
            missing.append(rel)
    if not os.path.isfile(os.path.join(root, MARKER_REL)):
        missing.append(MARKER_REL)
    if not os.path.isfile(os.path.join(root, LAUNCHER_REL)):
        # 只是一个便利文件（项目自己的启动器）：列出来提醒一下，但不影响 state
        missing.append(LAUNCHER_REL)
    seen = set()
    missing = [rel for rel in missing if not (rel in seen or seen.add(rel))]

    if not os.path.isfile(path):
        return {'root': root, 'state': 'missing', 'configfile': path,
                'config': None,
                'problems': ['No configuration file yet.'],
                'missing': missing}

    config = _read_json(path)
    if config is None:
        return {'root': root, 'state': 'broken', 'configfile': path,
                'config': None,
                'problems': ['The configuration file cannot be parsed as JSON.'],
                'missing': missing}

    problems = validate_config(config, translate=translate)
    required_missing = [p for p in problems if p.startswith('Missing server')
                        or p.startswith('Server "')
                        or p.startswith('The configuration')]
    if not required_missing:
        servers = config.get('Servers') or {}
        have = {str(k).replace(' ', '').lower() for k in servers}
        required_missing = [('Missing server: %s' % name) for name in
                            tuple('%s' % n for n, _s, _r in LOGGERS)
                            if name.replace(' ', '').lower() not in have]
    if required_missing:
        state = 'broken'
        problems = required_missing + problems
    elif not isinstance(config.get('GUI'), dict) or not config.get('GUI') \
            or not isinstance(config.get('files'), dict):
        state = 'incomplete'
    else:
        state = 'ok'
    return {'root': root, 'state': state, 'configfile': path,
            'config': config, 'problems': problems, 'missing': missing}


def _write_json(path, data):
    """原子写 JSON（先写同目录临时文件再 rename，避免半截文件）。

    Args:
        path (str): 目标路径。
        data (dict): 要写入的内容。
    """
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    handle = tempfile.NamedTemporaryFile(
        'w', encoding='utf-8', dir=directory, prefix='.tmp-', delete=False,
        suffix='.json')
    try:
        with handle:
            json.dump(data, handle, indent=1, ensure_ascii=False)
            handle.write('\n')
        os.replace(handle.name, path)
    except BaseException:
        try:
            os.unlink(handle.name)
        except OSError:
            pass
        raise


def remember_project_root(root):
    """把项目根写入用户级提示文件（任何目录启动都能找到项目）。

    与 :data:`quatm.licensing.tool._PROJECT_ROOT_HINT`、
    :data:`quatm.servers.configreader._PROJECT_ROOT_HINT` 是同一个文件；
    写失败不影响初始化结果。

    Args:
        root (str): 项目根目录。

    Returns:
        bool: 写入成功为 True。
    """
    try:
        with open(PROJECT_ROOT_HINT, 'w', encoding='utf-8') as handle:
            handle.write(os.path.abspath(str(root)))
        return True
    except OSError:
        return False


def check_writable(root):
    """检查目录能否创建/写入（失败时给出可执行的原因）。

    Args:
        root (str): 目标目录。

    Returns:
        str: 空串表示可写；否则是英文原因（供 ``tr()`` 翻译）。
    """
    root = os.path.abspath(str(root))
    try:
        os.makedirs(root, exist_ok=True)
    except OSError as exc:
        return tr('Cannot create the folder: %s') % exc
    probe = os.path.join(root, '.quatm-write-test')
    try:
        with open(probe, 'w', encoding='utf-8') as handle:
            handle.write('ok')
    except OSError as exc:
        return tr('The folder is not writable: %s') % exc
    finally:
        try:
            os.unlink(probe)
        except OSError:
            pass
    return ''


def merge_configfile(existing, new):
    """把 ``new`` 里**还没有**的条目补进 ``existing``（已有条目一律不覆盖）。

    用于“重新创建”：用户回到向导的清单页改了选项、再加一次时，只想把新勾的
    东西补上，而不是把已经跑起来的项目配置重写一遍。规则：

    * 整个分类不存在 → 整个分类搬过去；
    * 分类里条目不存在 → 只加这一条；
    * 条目两边都有 → **只同步 ``active`` 开关**（服务/程序的勾选语义就是它），
      ``script`` / ``arguments`` / ``group`` 等一律保持原样——那些字段可能被
      用户按本项目实际情况改过（指向本项目的脚本副本等）。

    Args:
        existing (dict): 现有 configfile 内容。
        new (dict): 新生成的 configfile 内容。

    Returns:
        tuple[dict, list[str], list[str]]: ``(合并结果, 新增条目, 改动的条目)``；
            条目写成 ``'Monitor/Camera A'`` 这种“分类/名字”形式。
    """
    merged = copy.deepcopy(existing)
    added = []
    changed = []
    for category, entries in (new or {}).items():
        if not isinstance(entries, dict):
            if category not in merged:
                merged[category] = copy.deepcopy(entries)
                added.append(category)
            continue
        target = merged.setdefault(category, {})
        for name, entry in entries.items():
            if name not in target:
                target[name] = copy.deepcopy(entry)
                added.append('%s/%s' % (category, name))
                continue
            old = target[name]
            if isinstance(old, dict) and isinstance(entry, dict) \
                    and 'active' in entry and old.get('active') != entry['active']:
                old['active'] = entry['active']
                changed.append('%s/%s' % (category, name))
    return merged, added, changed


def create_project(root, config=None, *, gui_active=DEFAULT_ACTIVE,
                   backup_broken=True, dry_run=False, instances=(),
                   services=None, drivers=(), monitors=(), templates=(),
                   monitor_templates=(), import_drivers=(),
                   import_monitors=(), import_experiments=(),
                   merge_entries=False):
    """在 ``root`` 下创建缺失的项目骨架（**绝不覆盖**已有文件）。

    Args:
        root (str): 项目根目录（不存在则创建）。
        config (dict|None): 要写入的 configfile 内容；None 时用
            :func:`default_configfile` 按下面的选项生成。
        gui_active (Iterable[str]): 默认启用的 GUI/Viewer 脚本名。
        backup_broken (bool): 目标已有 configfile 但解析失败时，先备份为
            ``configfile.json.broken-<时间戳>`` 再重建。
        dry_run (bool): True 时只计算要做什么，不落盘。
        instances (Iterable[dict]): 额外程序实例（多开/自定义命名）。
        services (Mapping[str, bool]|None): 服务启用开关；None = 全部启用。
        drivers (Iterable[dict]): 登记进 ``Driver`` 分类的条目（含 ``device_db``
            骨架）。
        monitors (Iterable[dict]): 登记进 ``Monitor`` 分类的条目。
        templates (Iterable[str]): 要落盘的示例实验文件名（见
            :data:`EXPERIMENT_TEMPLATES`）。
        monitor_templates (Iterable[str]): 要落盘的监视器模板文件名。
        import_drivers (Iterable[str]): 复制进 ``<root>/drivers/`` 的驱动文件。
        import_monitors (Iterable[str]): 复制进 ``<root>/monitor/`` 的监视器文件。
        import_experiments (Iterable[str]): 复制进 ``<root>/experiments/`` 的实验文件。

    Returns:
        dict: ``{'root', 'created', 'kept', 'backed_up', 'skipped',
            'problems'}``；``created``/``kept`` 里是相对路径（目录带尾斜杠）。

    Raises:
        ValueError: ``root`` 为空、不可写，或 ``config`` 内容不可用。
    """
    root = os.path.abspath(str(root or '').strip())
    if not root:
        raise ValueError('no project folder given')
    if not dry_run:
        problem = check_writable(root)
        if problem:
            raise ValueError(problem)

    payload = config if config is not None else default_configfile(
        gui_active, instances=instances, services=services, drivers=drivers,
        monitors=monitors)
    problems = validate_config(payload)
    if problems:
        raise ValueError(problems[0])

    report = {'root': root, 'created': [], 'kept': [], 'backed_up': [],
              'skipped': [], 'problems': problems,
              'added_entries': [], 'changed_entries': []}

    def make_dir(rel):
        """按需创建目录并记入报告。

        Args:
            rel (str): 相对项目根的目录路径。
        """
        path = os.path.join(root, *rel.split('/'))
        if os.path.isdir(path):
            report['kept'].append(rel + '/')
            return
        report['created'].append(rel + '/')
        if not dry_run:
            os.makedirs(path, exist_ok=True)

    def write_file(rel, data, dump=True):
        """按需创建文件（已存在则保留）。

        Args:
            rel (str): 相对项目根的路径。
            data (object): 内容。
            dump (bool): True 表示 ``data`` 是 JSON 可序列化对象。
        """
        path = os.path.join(root, *rel.split('/'))
        if os.path.isfile(path):
            if rel == CONFIGFILE_REL and _read_json(path) is None \
                    and backup_broken and not dry_run:
                backup = '%s.broken-%s' % (
                    path, datetime.datetime.now().strftime('%Y%m%d-%H%M%S'))
                shutil.copy2(path, backup)
                report['backed_up'].append(os.path.basename(backup))
                report['created'].append(rel)
                _write_json(path, data)
                return
            report['kept'].append(rel)
            return
        report['created'].append(rel)
        if not dry_run:
            if dump:
                _write_json(path, data)
            else:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                with open(path, 'w', encoding='utf-8') as handle:
                    handle.write(data)

    for rel in PROJECT_DIRS:
        make_dir(rel)
    config_path = os.path.join(root, *CONFIGFILE_REL.split('/'))
    existing = _read_json(config_path) if merge_entries else None
    if isinstance(existing, dict):
        merged, added, changed = merge_configfile(existing, payload)
        report['added_entries'] = added
        report['changed_entries'] = changed
        if added or changed:
            report['kept'].append(CONFIGFILE_REL)
            if not dry_run:
                _write_json(config_path, merged)
        else:
            report['kept'].append(CONFIGFILE_REL)
    else:
        write_file(CONFIGFILE_REL, payload)
    write_file(PROPERTIES_REL, {})
    write_file(EXPERIMENTS_README_REL, _EXPERIMENTS_README, dump=False)
    write_file(MARKER_REL, 'quatm project root\n', dump=False)
    # 项目自己的启动器：跟着项目走，新机器上“先跑它、它会弹向导”是最省事的上手
    # 路径（同名不覆盖——用户可能已经按自己习惯改过）。
    launcher = template_path('launcher', LAUNCHER_REL)
    if launcher:
        target = _copy_file(launcher, root, dry_run, suffix_on_conflict=False)
        if target:
            report['created'].append(os.path.relpath(target, root))
    # 设备库：**总是**给一份（空表 + 模板注释）。缺它时实验窗口以前会直接
    # 报 FileNotFoundError（新机器上打开任何示例实验都崩），现在即使不是
    # 向导也会生成一个可用骨架。
    write_file(DEVICE_DB_REL, device_db_source(drivers), dump=False)
    # 可选的示例/模板实验 + 监视器脚本（同名 = 已经有过了，跳过不复制）
    for name in templates or ():
        source = template_path('experiments', name)
        if source:
            target = _copy_file(source, os.path.join(root, EXPERIMENTS_DIR_NAME),
                                dry_run, suffix_on_conflict=False)
            if target:
                report['created'].append(os.path.relpath(target, root))
    for name in monitor_templates or ():
        source = template_path('monitor', name)
        if source:
            target = _copy_file(source, os.path.join(root, MONITOR_SUBDIR),
                                dry_run, suffix_on_conflict=False)
            if target:
                report['created'].append(os.path.relpath(target, root))
    # 用户自己的文件：驱动 / 监视器 / 已有实验，复制进项目（同名不覆盖）
    for source, subdir, rel_dir in (
            [(p, DRIVER_SUBDIR, DRIVER_SUBDIR) for p in import_drivers or ()]
            + [(p, MONITOR_SUBDIR, MONITOR_SUBDIR) for p in import_monitors or ()]
            + [(p, EXPERIMENTS_DIR_NAME, EXPERIMENTS_DIR_NAME)
               for p in import_experiments or ()]):
        target = _copy_file(source, os.path.join(root, subdir), dry_run)
        if target:
            report['created'].append(os.path.relpath(target, root))
    return report


#: 独立入口（用户可手动重跑向导；也是自动钩子拉起的子进程）。
#: 用环境变量 ``QUATM_FIRST_RUN_ENTRY`` 可以换成别的脚本——自动化部署里预先写好
#: 项目骨架、或测试里替身向导时用。
FIRST_RUN_ENTRY = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'GUI', 'entries', 'first_run.py')

#: 需要“完整配置”（GUI/Viewer/files 段）的入口：它们是控制台本身，缺这些段会看
#: 到空列表，所以由它们提示补齐；其它 GUI 缺可选段时不打扰用户。
NEEDS_FULL_CONFIG_ENTRIES = ('processmanager.py', 'first_run.py')

#: 项目自带的启动器：放在**项目根**（跟着项目走），双击/运行它就能启动控制中心，
#: 项目还没配好时它自己会先弹首次运行向导。
LAUNCHER_REL = 'start_quatm.py'

#: 认定为“控制中心启动器”的入口名（去掉非字母数字后比较）。用户在项目目录里跑
#: ``start_quatm.py``、``my_start_quatm.py`` 都要能自动触发首次运行向导——
#: “先拷一个启动器、再跑它”是新机器上最常见的上手路径。
LAUNCHER_ENTRY_STEMS = ('startquatm', 'quatmstart', 'launchquatm')

#: 子进程退出码约定（父进程据此区分“用户取消”和“环境不支持”）。
WIZARD_OK = 0
WIZARD_ERROR = 1
WIZARD_CANCELLED = 3


def running_entry_path():
    """本进程入口文件的路径（``__main__.__file__`` 优先，退回 ``sys.argv[0]``）。

    与 :func:`quatm.licensing._running_entry_path` 同一套取值规则：交互式解释器
    或 ``-m`` 启动时 ``sys.argv[0]`` 可能是别的东西，不能只信它。

    Returns:
        str: 路径；取不到时为空串。
    """
    module = sys.modules.get('__main__')
    path = getattr(module, '__file__', None)
    if not path and sys.argv:
        path = sys.argv[0]
    return str(path or '')


def running_entry():
    """本进程入口脚本的文件名（如 ``browser.py``）。

    Returns:
        str: 文件名；取不到时为空串。
    """
    return os.path.basename(running_entry_path())


def _squash(text):
    """去掉非字母数字并小写（宽松比较启动器名字用）。

    Args:
        text (str): 原始文字。

    Returns:
        str: 归一字串。
    """
    return re.sub(r'[^a-z0-9]', '', str(text).lower())


def looks_like_gui_entry(entry=None, path=None):
    """判断当前入口是不是“图形/控制中心”入口（决定要不要自动弹向导）。

    **不能只看入口脚本名在不在 APP_META 里**：启动方式多种多样，实测就踩到过
    “装了启动器但向导不弹”。按可靠性从高到低判断：

    1. 入口文件在 ``quatm/GUI/`` 下 —— 任何启动方式都成立的硬规则，与
       :func:`quatm.licensing.is_gui_process` 一致；
    2. 入口文件在 ``quatm/web/`` 下 —— web 控制中心同样要读 configfile；
    3. 入口脚本名在 ``quatm/GUI/infra/app_meta.py`` 身份表里（终端里直接跑
       ``python quatm/GUI/entries/browser.py``）；
    4. 入口名（去非字母数字后）与身份表里的脚本名/显示名/desktop id 相等或
       互为结尾（启动器用 ``bin/<desktop id>`` 或 ``.app`` 名启动的情形）；
    5. 入口名是项目启动器（:data:`LAUNCHER_ENTRY_STEMS`，如 ``start_quatm.py``）
       —— 这类脚本自己就在项目目录里、干的就是“起控制中心”的活，理应弹向导。

    Args:
        entry (str|None): 入口脚本名（默认 :func:`running_entry`）。
        path (str|None): 入口文件路径（默认 :func:`running_entry_path`）。

    Returns:
        bool: 视为图形/控制中心入口为 True。
    """
    entry = entry if entry is not None else running_entry()
    raw_path = running_entry_path() if path is None else path
    lowered = str(raw_path).replace('\\', '/').lower()
    if '/quatm/gui/' in lowered or '/quatm/web/' in lowered:
        return True
    stem = _squash(os.path.splitext(entry or '')[0])
    if stem and any(stem == launcher or stem.endswith(launcher)
                    for launcher in LAUNCHER_ENTRY_STEMS):
        return True
    meta = app_meta()
    if entry and entry in meta:
        return True
    if not stem:
        return False
    for script, (name, desktop_id, _icon) in meta.items():
        script_name = _squash(os.path.splitext(script)[0])
        for candidate in (script_name, _squash(desktop_id), _squash(name)):
            if not candidate:
                continue
            if stem == candidate or stem.endswith(candidate) \
                    or candidate.endswith(stem):
                return True
        if script_name and stem.endswith(script_name):
            return True
    return False


def ensure_project_ready(entry=None, root=None, quiet=False, force=False):
    """入口自检：项目没配好就拉起首次运行向导（**用子进程**）。

    为什么用子进程而不是进程内弹窗：向导要自己的 ``QApplication``，而入口脚本
    随后还会 ``QApplication(sys.argv)`` —— PySide6 里 QApplication 是单例，
    进程内先建一个会让入口直接 RuntimeError。子进程跑完即退，父进程保持“干净”，
    创建结果通过项目根提示文件回传（:func:`remember_project_root`）。

    本函数只依赖标准库与本模块（不 import Qt、不 import ``quatm.utils``），
    因此在**未激活**的全新机器上也能先跑起来。

    Args:
        entry (str|None): 当前入口脚本名（默认取实际入口文件名）。
        root (str|None): 项目根（默认按 :func:`find_project_root` 定位）。
        quiet (bool): True 时不打印说明（自动化/测试用）。
        force (bool): True 时无视“是不是 GUI 入口”的判断（命令行/测试用）。

    Returns:
        bool: 项目现在可用（或用户选择稍后自行补全可选部分）时为 True；
        完全不可用（用户取消、无显示环境、创建失败）时为 False，调用方应退出。
        非 GUI 入口（服务器/实验子进程）恒为 True：它们不弹窗，缺配置时由
        :func:`quatm.servers.configreader.Config` 报出可执行的错误说明。
    """
    entry = entry if entry is not None else running_entry()
    if entry in ('first_run.py', 'project_setup.py'):
        return True                      # 向导自己在跑，不能递归
    start = os.path.abspath(str(root)) if root else find_project_root()
    info = inspect_project(start)
    is_gui = bool(force) or looks_like_gui_entry(entry)
    if os.environ.get('QUATM_FIRST_RUN_DEBUG'):
        sys.stderr.write(
            '[quatm] first-run check: entry=%r path=%r gui=%s state=%s root=%s\n'
            % (entry, running_entry_path(), is_gui, info['state'], start))
    if info['state'] == 'ok':
        return True
    if not is_gui:
        # 服务器/实验子进程：不弹窗。如果是人在终端里敲的，给一句可执行的提示。
        if info['state'] not in ('ok',) and not quiet and sys.stderr.isatty():
            sys.stderr.write('[quatm] %s\n' % _state_note(info))
            sys.stderr.write('[quatm] start a graphical entry to create it, or run: '
                             'python -m quatm.project_setup --create --root "%s"\n'
                             % start)
        return True
    if info['state'] == 'incomplete' and entry not in NEEDS_FULL_CONFIG_ENTRIES:
        # 能跑：只是缺记录器/GUI 段，别每次启动都拦人
        return True
    if os.environ.get('QUATM_SKIP_FIRST_RUN'):
        return False

    def note(text):
        """按 quiet 设置打印一行说明。

        Args:
            text (str): 说明文字。
        """
        if not quiet:
            sys.stderr.write('%s\n' % text)

    entry_script = (os.environ.get('QUATM_FIRST_RUN_ENTRY', '').strip()
                    or FIRST_RUN_ENTRY)
    note('[quatm] %s' % _state_note(info))
    note('[quatm] starting the first-run setup wizard: %s' % entry_script)
    try:
        code = subprocess.call([sys.executable, entry_script, '--root', start])
    except OSError as exc:
        note('[quatm] cannot start the setup wizard: %s' % exc)
        note('[quatm] run it manually: python %s --root "%s"'
             % (entry_script, start))
        return False
    if code == WIZARD_ERROR:
        note('[quatm] the setup wizard could not run (no display?). Run it on a '
             'desktop session, or create the project from a terminal:')
        note('        python -m quatm.project_setup --create --root "%s"' % start)
        return False

    chosen = _read_project_root_hint() or start
    info = inspect_project(chosen)
    if info['state'] in ('ok', 'incomplete'):
        # 让本进程（以及它拉起的子进程）都用新建的项目
        os.environ['QUATM_PROJECT_ROOT'] = chosen
        return True
    if code == WIZARD_CANCELLED:
        note('[quatm] setup cancelled — quatm cannot run without a project '
             'configuration.')
        note('        re-run: python -m quatm.project_setup --create --root "%s"'
             % start)
    return False


def _read_project_root_hint():
    """读取项目根提示文件里的路径（向导写、由本模块读回）。

    Returns:
        str: 目录路径；文件不存在/内容不是有效目录时返回空串。
    """
    try:
        with open(PROJECT_ROOT_HINT, encoding='utf-8') as handle:
            root = handle.read().strip()
    except OSError:
        return ''
    return root if root and os.path.isdir(root) else ''


#: 项目骨架里实验脚本的目录名（相对项目根）。
EXPERIMENTS_DIR_NAME = 'experiments'

#: 项目骨架之外还要建的配置文件（向导里可关掉的那几项）。
DEVICE_DB_REL = os.path.join('configuration', 'device_db.py')

#: 随包分发的模板目录（示例实验 / 监视器 / device_db）。
TEMPLATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            'templates')

#: 可选实验模板：(文件名, 说明（英文原文，供 tr()）, 新项目里默认勾选？)。
#: 前两个是**开箱即跑的示例**（新项目的 ``experiments/`` 里默认带上），后面的
#: ``template_*`` / 专题示例要用就勾。Browser 的“新建实验”对话框读同一张表，
#: 所以向导里勾得到的与新建实验时选得到的始终是同一套模板。
EXPERIMENT_TEMPLATES = (
    ('example_parameters.py', 'Parameters and controls (start here)', True),
    ('example_scan.py', 'Scan experiment (runs one point per run)', True),
    ('template_experiment.py', 'Blank skeleton (build + run, start here)',
     False),
    ('example_realtime.py', 'Realtime sequence (fixed-step timing)', False),
    ('example_devices.py', 'Devices from device_db (read / write)', False),
)

#: Browser 的“新建实验”对话框默认选中的模板。
DEFAULT_NEW_TEMPLATE = 'template_experiment.py'

#: 可选监视器模板：(文件名, 说明)。
MONITOR_TEMPLATES = (
    ('example_monitor.py', 'Monitor script skeleton (periodic read)'),
)

#: 导入用户文件时落盘的子目录：驱动与监视器分开存，便于 ``device_db`` 引用。
DRIVER_SUBDIR = 'drivers'
MONITOR_SUBDIR = 'monitor'


def template_files(kind):
    """列出某类内置模板里的全部文件名（去掉说明）。

    Args:
        kind (str): ``'experiments'`` 或 ``'monitor'``。

    Returns:
        list[str]: 文件名列表。
    """
    table = EXPERIMENT_TEMPLATES if kind == 'experiments' else MONITOR_TEMPLATES
    return [item[0] for item in table]


def experiment_template_choices():
    """实验模板目录：给 GUI“新建实验”对话框用的 ``(文件名, 说明, 默认选?)`` 列表。

    顺序把 ``template_*`` 骨架放在前面（新建实验的默认选择），示例放在后面——
    两者来自同一张 :data:`EXPERIMENT_TEMPLATES`，不会出现“向导里有、新建实验时
    找不到”这种不一致。

    Returns:
        list[tuple[str, str, bool]]: 每项 ``(模板文件名, 英文说明, 是否默认选)
``。
    """
    items = list(EXPERIMENT_TEMPLATES)
    items.sort(key=lambda item: (not item[0].startswith('template'),))
    return items


def template_path(kind, name):
    """返回内置模板文件的绝对路径。

    Args:
        kind (str): ``'experiments'`` / ``'monitor'`` / ``'device_db'`` /
            ``'launcher'``。
        name (str): 文件名（``device_db`` / ``launcher`` 时忽略）。

    Returns:
        str: 模板文件路径；不存在时返回空串。
    """
    if kind == 'device_db':
        path = os.path.join(TEMPLATE_DIR, 'device_db.py')
    elif kind == 'launcher':
        path = os.path.join(TEMPLATE_DIR, LAUNCHER_REL)
    else:
        path = os.path.join(TEMPLATE_DIR, kind, name)
    return path if os.path.isfile(path) else ''


def device_db_source(drivers=()):
    """生成 ``configuration/device_db.py`` 的文本（模板 + 已登记驱动的骨架）。

    骨架只是把“要填什么”列出来（``module``/``class``/``arguments``），真实取值
    必须由用户核实——向导不能猜他的仪器地址。

    Args:
        drivers (Iterable[dict]): 每项 ``{'name', 'script'}``（script 是项目内
            相对路径，如 ``drivers/my_device.py``）。

    Returns:
        str: device_db.py 的内容。
    """
    path = template_path('device_db', 'device_db.py')
    try:
        with open(path, encoding='utf-8') as handle:
            text = handle.read()
    except OSError:
        text = 'device_db = {}\n'
    entries = []
    for item in drivers or ():
        name, script = _named_entry(item)
        if not name:
            continue
        module = os.path.splitext(str(script).replace('/', '.'))[0]
        entries.append(
            '    # TODO 向导登记：请核实模块/类名与参数（仪器地址、通道号等）。\n'
            '    %r: {\n'
            '        "type": "local",\n'
            '        "module": %r,\n'
            '        "class": "TODO_ClassName",\n'
            '        "arguments": {},\n'
            '    },\n' % (name, module))
    if not entries:
        return text
    marker = 'device_db = {'
    index = text.find(marker)
    if index < 0:
        return text + '\n' + ''.join(entries)
    insert = index + len(marker)
    return text[:insert] + '\n' + ''.join(entries) + text[insert:]


def _copy_file(source, target_dir, dry_run=False, suffix_on_conflict=True):
    """把文件复制进项目子目录；**不覆盖**同名文件。

    Args:
        source (str): 源文件路径。
        target_dir (str): 目标目录（不存在则创建）。
        dry_run (bool): True 时只算路径不落盘。
        suffix_on_conflict (bool): 同名时是否自动加 ``-2``/``-3`` 后缀。
            默认 True，用于**用户自己的文件**（不能覆盖人家同名的脚本）；
            模板传 False：同名就当“已经有了”跳过——否则用户从完成页“返回”后
            再“重新创建”一次，项目里就多出一份 ``example_scan-2.py``。

    Returns:
        str: 实际写入的路径；源文件不存在、或不允许重名而目标已存在时返回空串。
    """
    if not os.path.isfile(source):
        return ''
    base = os.path.basename(source)
    stem, ext = os.path.splitext(base)
    target = os.path.join(target_dir, base)
    if not suffix_on_conflict:
        if os.path.exists(target):
            return ''
        if not dry_run:
            os.makedirs(target_dir, exist_ok=True)
            shutil.copy2(source, target)
        return target
    index = 2
    while os.path.exists(target):
        target = os.path.join(target_dir, '%s-%d%s' % (stem, index, ext))
        index += 1
    if not dry_run:
        os.makedirs(target_dir, exist_ok=True)
        shutil.copy2(source, target)
    return target


def _write_text(path, text, dry_run=False):
    """写一个文本文件（已存在则不动）。

    Args:
        path (str): 目标路径。
        text (str): 内容。
        dry_run (bool): True 时只算路径不落盘。
    """
    if not dry_run:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w', encoding='utf-8') as handle:
            handle.write(text)


def _state_note(info):
    """给入口自检打印用的**纯英文**一句话（故意不翻译）。

    Args:
        info (dict): :func:`inspect_project` 的返回值。

    Returns:
        str: 状态说明。
    """
    text = {
        'missing': 'no project configuration in %s',
        'broken': 'the project configuration in %s is not usable',
        'incomplete': 'the project configuration in %s is missing optional parts',
        'ok': 'project configuration in %s is ready',
    }
    return (text.get(info['state'], '%s') % info['root'])


def summary_lines(info, report=None, preview=False):
    """把自检结果（和可选的创建报告）整理成给用户看的行。

    Args:
        info (dict): :func:`inspect_project` 的返回值。
        report (dict|None): :func:`create_project` 的返回值；传 ``dry_run`` 的
            结果时配合 ``preview=True``。
        preview (bool): True 表示这是“将要做什么”的预览（还没真的写盘），
            首行不说“已创建”。

    Returns:
        list[str]: 每行一条。
    """
    state_text = {
        'ok': tr('This folder is already configured.'),
        'incomplete': tr('A usable configuration exists, but some optional parts '
                         'are missing.'),
        'broken': tr('The configuration here exists but is not usable.'),
        'missing': tr('No configuration in this folder yet — it will be created.'),
    }
    lines = []
    created_files = bool(report and (report.get('created')
                                     or report.get('backed_up')))
    entry_updates = bool(report and (report.get('added_entries')
                                     or report.get('changed_entries')))
    if report and (created_files or entry_updates):
        if created_files:
            lines.append(tr('Files to create in this folder:')
                         if preview else tr('Project configuration created.'))
        else:
            lines.append(tr('Entries to add or update in this folder:')
                         if preview else tr('Project configuration updated.'))
    else:
        lines.append(state_text.get(info['state'], info['state']))
    if report:
        for rel in report['created']:
            lines.append(tr('create: %s') % rel)
        for rel in report.get('added_entries', ()):
            lines.append(tr('add entry: %s') % rel)
        for rel in report.get('changed_entries', ()):
            lines.append(tr('update entry: %s') % rel)
        for rel in report['kept']:
            lines.append(tr('keep existing: %s') % rel)
        for name in report['backed_up']:
            lines.append(tr('backup: %s') % name)
    else:
        for rel in info['missing']:
            lines.append(tr('missing: %s') % rel)
    if info['state'] == 'broken':
        for problem in (info.get('problems') or [])[:8]:
            lines.append(tr('problem: %s') % problem)
    return lines


def main(argv=None):
    """命令行入口：``python -m quatm.project_setup --check|--create``。

    Args:
        argv (list[str]|None): 参数列表（默认 ``sys.argv[1:]``）。

    Returns:
        int: 退出码（0 成功；1 有问题）。
    """
    parser = argparse.ArgumentParser(
        prog='python -m quatm.project_setup',
        description=('Check or create the quatm project layout '
                     '(configuration/configfile/configfile.json, experiments/, '
                     'Data/, images/).'))
    parser.add_argument('--root', default='',
                        help='project folder (default: detected project root, '
                             'otherwise the current directory)')
    parser.add_argument('--check', action='store_true',
                        help='only report the current state')
    parser.add_argument('--create', action='store_true',
                        help='create the missing files/folders')
    parser.add_argument('--gui', default=','.join(DEFAULT_ACTIVE),
                        help='comma separated entry scripts to enable '
                             '(default: %(default)s)')
    parser.add_argument('--dry-run', action='store_true',
                        help='with --create: show what would be created')
    parser.add_argument('--merge', action='store_true',
                        help='with --create: keep an existing configfile but add '
                             'the entries that are missing from it (and sync the '
                             'active flags), instead of leaving it untouched')
    parser.add_argument('--json', action='store_true',
                        help='machine readable output')
    parser.add_argument('--quiet', action='store_true',
                        help='only print problems')
    args = parser.parse_args(argv)

    root = args.root.strip() or find_project_root()
    info = inspect_project(root, translate=True)
    if not args.create:
        if args.json:
            print(json.dumps({'state': info['state'], 'root': info['root'],
                              'configfile': info['configfile'],
                              'problems': info['problems'],
                              'missing': info['missing']},
                             ensure_ascii=False, indent=1))
        elif not args.quiet:
            print('project root: %s' % info['root'])
            print('state: %s' % info['state'])
            for line in summary_lines(info):
                print('  %s' % line)
        return 0 if info['state'] == 'ok' else 1

    gui_active = tuple(x.strip() for x in str(args.gui or '').split(',')
                       if x.strip())
    try:
        report = create_project(root, gui_active=gui_active,
                                dry_run=args.dry_run,
                                merge_entries=args.merge)
    except ValueError as exc:
        print('cannot set up %s: %s' % (root, exc))
        return 1
    if not args.dry_run:
        remember_project_root(root)
    info = inspect_project(root, translate=True)
    if args.json:
        print(json.dumps({'state': info['state'], 'root': root,
                          'created': report['created'], 'kept': report['kept'],
                          'backed_up': report['backed_up'],
                          'problems': info['problems']},
                         ensure_ascii=False, indent=1))
    elif not args.quiet:
        print('project root: %s' % root)
        for line in summary_lines(info, report):
            print('  %s' % line)
        if args.dry_run:
            print('  (dry run: nothing was written)')
    return 0 if info['state'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
