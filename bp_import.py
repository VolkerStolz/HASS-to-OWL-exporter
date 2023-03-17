import yaml
import re
import urllib.parse
import urllib.request
import requests_cache
from homeassistant.helpers.blueprints import BLUEPRINT_SCHEMA


class Input(yaml.YAMLObject):
    yaml_loader = yaml.SafeLoader
    yaml_tag = '!input'

    def __init__(self, name):
        self.name = name

    def __repr__(self):
        return f"Input({self.name})"

    @classmethod
    def from_yaml(cls, loader, node):
        return cls(node.value)


# with urllib.request.urlopen("https://gist.githubusercontent.com/sbyx/96c43b13b90ae1c35b872313ba1d2d2d/raw/fc5dba10a35b882b74f42b6209d60e0084368212/wake-up-light-alarm-with-sunrise-effect.yaml") as r:
# print(yaml.safe_load(r.read()))

gists = [
    "https://gist.githubusercontent.com/sbyx/96c43b13b90ae1c35b872313ba1d2d2d/raw/fc5dba10a35b882b74f42b6209d60e0084368212/wake-up-light-alarm-with-sunrise-effect.yaml"
]

my_imports = [
    "https://my.home-assistant.io/redirect/blueprint_import/?blueprint_url=https%3A%2F%2Fgithub.com%2Fgmlupatelli%2Fblueprints_repo%2Fblob%2Fmaster%2Flow_battery_notification%2Flow_battery_notification.yaml"
]

MY_IMPORT_DECODER = re.compile(
    r"^.*blueprint_url=(.*)$"
)

GITHUB_FILE_PATTERN = re.compile(
    r"^https://github.com/(?P<repository>.+)/blob/(?P<path>.+)$"
)

for m in my_imports:
    try:
        url = MY_IMPORT_DECODER.match(urllib.parse.unquote(m)).group(1)
        match = GITHUB_FILE_PATTERN.match(url)
        repo, path = match.groups()
        gists.append(f"https://raw.githubusercontent.com/{repo}/{path}")
    except:
        pass  # error


def get_entity(s):
    return s['entity']


my_keys = ['domain', 'integration', 'device_class']
deps = {}
for mk in my_keys:
    deps[mk] = set()

u = requests_cache.CachedSession('demo_cache')
for bp in gists:
    print("Processing: "+bp)
    date = u.get(bp)
    try:
        r = BLUEPRINT_SCHEMA(yaml.safe_load(date.text))
        for i in r['blueprint']['input']:
            s = r['blueprint']['input'][i]['selector']
            print(i, s)
            if 'entity' in s:
                ys = get_entity(s)
            elif 'target' in s:
                ys = get_entity(s['target'])
            print("xs", ys)
            if not(isinstance(ys, list)):
                ys = [ys]
            for xs in ys:
                for mk in my_keys:
                    if mk in xs:
                        elems = deps[mk]
                        deps[mk] = elems.union(set(xs[mk]))
    except:
        print("Failed: "+u)
    print("Result ", deps)
