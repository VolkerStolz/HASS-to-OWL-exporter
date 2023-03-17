import yaml
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


u = requests_cache.CachedSession('demo_cache')
date = u.get("https://gist.githubusercontent.com/sbyx/96c43b13b90ae1c35b872313ba1d2d2d/raw/fc5dba10a35b882b74f42b6209d60e0084368212/wake-up-light-alarm-with-sunrise-effect.yaml")
r = BLUEPRINT_SCHEMA(yaml.safe_load(date.text))
inputs = [i for i in r['blueprint']['input'].keys()]
print(inputs)
