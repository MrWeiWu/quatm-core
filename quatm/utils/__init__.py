# 激活检查**不在这里**（AGPL 子包不得在 import 期依赖闭源 licensing）：
# 入口层统一调用 quatm.licensing.require_license()，边界见
# scripts/checks/license_boundary.py。

from .paths import (PROJECT_ROOT, CONFIG_DIR, EXPERIMENTS_DIR, DATA_DIR,
                    IMAGES_DIR, QUATM_DIR, resolve_experiment_path)
