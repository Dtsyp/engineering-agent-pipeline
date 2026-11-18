"""Per-host model preferences and the policy frozen when a task starts."""
import json
import os
from pathlib import Path
import re

REPO = Path(__file__).resolve().parents[1]
HOSTS = ('opencode', 'codex', 'claude')
ARCHS = tuple(f'pipeline-architect-l{i}' for i in (1, 2, 3))
REVIEWERS = tuple(f'pipeline-{stage}-{focus}' for stage in ('design', 'code') for focus in 'ab')
ROLES = ('pipeline-orchestrator', 'pipeline-analyst', *ARCHS, *REVIEWERS[:2],
         'pipeline-implementer', *REVIEWERS[2:])
PRESETS = ('inherit', 'max-quality', 'balanced', 'budget')
MODES = ('contexts', 'families')
SELECTOR = re.compile(r'[a-zA-Z0-9][a-zA-Z0-9._:/#@+-]{0,255}')
FAMILY = re.compile(r'[a-z0-9][a-z0-9._-]{0,79}')
# Only the exact selectors in the shipped example profiles are attributed here.
# An arbitrary gateway or host-native alias needs an explicit operator declaration.
PRESET_FAMILIES = {
    'openai': 'openai', 'moonshotai': 'kimi', 'z-ai': 'glm',
    'qwen': 'qwen', 'deepseek': 'deepseek',
}


def data_dir():
    explicit = os.environ.get('ENGPIPE_CONFIG_HOME')
    if explicit:
        return Path(explicit).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('APPDATA') or Path.home() / 'AppData' / 'Roaming') / 'engpipe'
    return Path(os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config') / 'engpipe'


def profile_policy(name, host):
    if name not in PRESETS or host not in HOSTS:
        raise ValueError('unknown profile or host')
    policy = {'host': host, 'mode': 'contexts', 'models': dict.fromkeys(ROLES), 'families': {}}
    if name == 'inherit':
        return policy
    if host != 'opencode':
        raise ValueError('named presets use OpenCode selectors. Use inherit and native model set for ' + host)
    models = json.loads((REPO / 'profiles' / (name + '.json')).read_text(encoding='utf8'))
    if set(models) != set(ROLES):
        raise ValueError('profile must declare each role once')
    families = {}
    for selector in models.values():
        parts = selector.split('/')
        maker = parts[1] if parts[0] == 'openrouter' and len(parts) > 2 else parts[0]
        if maker not in PRESET_FAMILIES:
            raise ValueError('profile has no declared family for ' + selector)
        families[selector] = PRESET_FAMILIES[maker]
    policy.update(mode='families', models=models, families=families)
    validate_routes(policy)
    return policy


def legacy_routing():
    return profile_policy('max-quality', 'opencode')


def config_path(host):
    if host not in HOSTS:
        raise ValueError('unsupported host: ' + str(host))
    return data_dir() / ('routing-' + host + '.json')


def task_policy(host):
    path = config_path(host)
    if not path.exists():
        return profile_policy('inherit', host)
    data = json.loads(path.read_text(encoding='utf8'))
    if not isinstance(data, dict) or data.get('mode') not in MODES or data.get('host') != host:
        raise ValueError('invalid routing policy: ' + str(path))
    models, families = data.get('models'), data.get('families')
    if not isinstance(models, dict) or set(models) != set(ROLES) or not isinstance(families, dict):
        raise ValueError('invalid role or family map in ' + str(path))
    for selector in models.values():
        if selector is not None:
            validate_selector(host, selector)
    if any(not SELECTOR.fullmatch(key) or not isinstance(value, str) or not FAMILY.fullmatch(value)
           for key, value in families.items()):
        raise ValueError('invalid declared family in ' + str(path))
    return data


def validate_selector(host, selector):
    if not isinstance(selector, str) or not SELECTOR.fullmatch(selector):
        raise ValueError('invalid host model selector: ' + str(selector))
    if host == 'opencode' and ('/' not in selector or not all(selector.split('/'))):
        raise ValueError('OpenCode selectors require provider/model')


def validate_review_models(policy, models):
    if policy['mode'] == 'contexts':
        return
    if policy['mode'] != 'families' or len(models) != 3:
        raise ValueError('expected a policy and the author plus two review identities')
    families = [policy['families'].get(model) for model in models]
    if None in families:
        raise ValueError('strict review requires explicit declared families for the author and both reviewers')
    if len(set(families)) != 3:
        raise ValueError('review model family collision')


def validate_routes(policy):
    for author in ARCHS:
        validate_review_models(policy, [policy['models'][key] for key in (author, *REVIEWERS[:2])])
    validate_review_models(policy, [policy['models'][key] for key in ('pipeline-implementer', *REVIEWERS[2:])])
