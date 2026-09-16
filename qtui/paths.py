# -*- coding: utf-8 -*-
"""应用的本机配置目录，各模块统一从这里取，避免到处硬编码 ``~/.smart_table_hub``。"""

import os

CONFIG_DIR = os.path.join(os.path.expanduser("~"), ".smart_table_hub")
