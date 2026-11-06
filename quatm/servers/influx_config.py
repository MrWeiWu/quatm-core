"""InfluxDB 连接配置解析（AGPL 侧的共享实现）。

**为什么单独成模块**：仓库里有多个 InfluxDB 使用方（``quatm/servers/influxdb``
服务端、``quatm/clients/influxdb`` 客户端、``quatm/physics`` 的绘图工具、
``quatm/analysis`` / ``quatm/monitor`` 的后台脚本）。解析规则必须只有一份，
否则又会出现"每个文件一套配置名"。放在 ``quatm.servers``（AGPL）里，AGPL 的
``quatm.physics`` 才能合法复用它——不能让 AGPL 的 physics 去 import 专有的
``quatm.clients``（边界见 ``scripts/checks/license_boundary.py``）。

**解析优先级**（逐项解析，配置文件优先）：

1. ``configuration/configfile/configfile.json`` 的 ``Servers/InfluxDB`` 段；
2. 环境变量 ``INFLUX_URL`` / ``INFLUX_TOKEN`` / ``INFLUX_ORG`` /
   ``INFLUX_HOST`` / ``INFLUX_PORT``——补配置文件里缺的项；
3. 仍缺失时抛出 :class:`InfluxDBConfigError`，**绝不回退到内置凭据**。

错误信息只列缺失的键名与环境变量名，**任何情况下都不打印 token**。
"""

import os
import sys

from quatm.servers import send_error
from quatm.servers.configreader import ConfigReader
from quatm.utils.i18n import tr

#: 连接参数 → 环境变量名（与 configfile ``Servers/InfluxDB`` 段的键一一对应）。
_ENV_KEYS = {
    'url': 'INFLUX_URL',
    'token': 'INFLUX_TOKEN',
    'org': 'INFLUX_ORG',
    'host': 'INFLUX_HOST',
    'port': 'INFLUX_PORT',
}

#: 客户端（写点/查询）需要的参数。
CLIENT_KEYS = ('url', 'token', 'org')

#: RPC 服务端需要的参数（还要监听 host/port）。
SERVER_KEYS = ('url', 'token', 'org', 'host', 'port')


class InfluxDBConfigError(RuntimeError):
    """缺少 InfluxDB 连接配置（错误信息只列缺失项，绝不包含 token 值）。"""


def _config_error_text(missing):
    """构造缺配置的可读错误文本。

    只出现环境变量名与配置路径，**不含任何凭据值**。

    Args:
        missing (list[str]): 缺失的键名（url/token/org/host/port 的子集）。

    Returns:
        str: 多行错误说明（已过 :func:`quatm.utils.i18n.tr`）。
    """
    env_names = ', '.join(_ENV_KEYS[key] for key in missing)
    keys = ', '.join(missing)
    return '\n'.join((
        tr('[InfluxDB] Missing connection settings (configfile '
           'Servers/InfluxDB or environment): {keys}', keys=env_names),
        tr('[InfluxDB] No credentials are bundled with quatm. Put '
           '{fields} in configuration/configfile/configfile.json under '
           'Servers -> InfluxDB or provide the matching INFLUX_* environment '
           'variables.', fields=keys),
        tr('[InfluxDB] The token is never logged. Fill in the settings and '
           'retry.'),
    ))


def resolve_influx_config(required=CLIENT_KEYS):
    """解析 InfluxDB 连接配置：configfile → 环境变量 → 缺失即报错。

    参数按**逐项**优先级合并：配置文件里有值的键优先，缺的键用同名环境变量
    补。**没有任何内置默认值**。

    Args:
        required (tuple[str, ...]): 必填的键；默认客户端三项，服务端用
            :data:`SERVER_KEYS`。

    Returns:
        dict: 解析出的连接配置（至少含 ``required`` 里的键）。

    Raises:
        InfluxDBConfigError: 缺少必填参数（错误信息只列缺失的键名，不含 token 值）。
    """
    file_conf = {}
    try:
        file_conf = (ConfigReader.getConfiguration()
                     .get('Servers', {})
                     .get('InfluxDB', {}))
    except Exception:  # noqa: BLE001 - 没有 configfile 时按“未配置”处理
        file_conf = {}
    if not isinstance(file_conf, dict):
        file_conf = {}

    conf = {}
    for key in tuple(required):
        value = file_conf.get(key)
        value = str(value).strip() if value is not None else ''
        if not value:
            value = os.environ.get(_ENV_KEYS[key], '').strip()
        conf[key] = value

    missing = [key for key in required if not conf[key]]
    if missing:
        raise InfluxDBConfigError(_config_error_text(missing))
    return conf


def report_influx_config_error(error):
    """把缺配置错误打印到 stderr 并发一条 ``send_error``（消息不含 token）。

    Args:
        error (InfluxDBConfigError | str): 缺配置错误（或其可读文本）。
    """
    message = str(error)
    print(message, file=sys.stderr)
    try:
        send_error(message)
    except Exception:  # noqa: BLE001 - 消息中枢不可用时保留本地报错即可
        pass
