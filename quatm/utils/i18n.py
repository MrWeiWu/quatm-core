"""i18n 转发垫片：实现统一在 :mod:`quatm.i18n`。

历史：实现曾经放在本模块（``quatm/utils/i18n.py``），后来收拢到
``quatm.i18n``；但迁移时旧实现没有删干净，本模块里仍留着一份自己的
``_state``/``tr()``/``set_language()``，导致 GUI（经
``quatm.GUI.infra.i18n`` 转发到 ``quatm.i18n``）与非 GUI（直接 import 本模块）
看到**两套独立的语言状态**——例如 GUI 已切到中文时 ``send_warning`` 仍用另一套
状态，测试/运行时语言不一致。

现在这里**只做转发**（与 ``quatm.GUI.infra.i18n`` 相同的做法）：对外接口与
:mod:`quatm.i18n` 完全一致，转发的是同一批函数对象，所以
``quatm.utils.i18n.tr is quatm.i18n.tr`` 成立。不要再往本文件里写实现逻辑。
"""

from quatm.i18n import *  # noqa: F401,F403
from quatm.i18n import (  # noqa: F401
    LANGUAGES,
    SOURCE_LANGUAGE,
    available_languages,
    catalog,
    catalog_paths,
    init_i18n,
    install_qt_translations,
    language,
    load_catalog,
    locale_dir,
    localized_label,
    missing_keys,
    normalize_language,
    project_locale_dir,
    project_settings_path,
    set_language,
    system_language,
    tr,
)


def __getattr__(name):
    """把实现模块里未显式列出的名字也转发过来。

    Args:
        name (str): 要取的名字。

    Returns:
        object: 实现模块里的同名对象。

    Raises:
        AttributeError: 实现模块里也没有这个名字。
    """
    import quatm.i18n as _impl
    try:
        return getattr(_impl, name)
    except AttributeError:
        raise AttributeError(
            'module %r has no attribute %r' % (__name__, name)) from None
