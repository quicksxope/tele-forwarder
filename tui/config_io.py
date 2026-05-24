import os
import uuid
from pathlib import Path
from ruamel.yaml import YAML

_yaml = YAML()
_yaml.preserve_quotes = True


def load_config(path=None):
    if path is None:
        from paths import CONFIG_PATH
        path = CONFIG_PATH
    with open(path) as f:
        return _yaml.load(f)


def save_config(cfg, path=None):
    if path is None:
        from paths import CONFIG_PATH
        path = CONFIG_PATH
    path = Path(path)
    tmp = path.parent / (path.name + '.tmp')
    with open(tmp, 'w') as f:
        _yaml.dump(cfg, f)
    os.replace(tmp, path)


def ensure_rule_uuid(rule) -> None:
    if 'uuid' not in rule or not rule['uuid']:
        rule['uuid'] = str(uuid.uuid4())
