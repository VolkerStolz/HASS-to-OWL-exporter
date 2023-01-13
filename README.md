# Instructions

0. Ignore the `Makefile`
1. Create a file `config.py` with:
```
hass_url = "http://your.do.main:8123/api/"
hass_token = "..."
```
2. Run `hacvt.py`, grab generated file and import e.g. into Protégé. You may need to tweak the filename in the code.

# Development tips

* `git clone git@github.com:home-assistant/core.git`, which is easier to browse than the package import.

# To-Dos

* Some part of the HASS-profile (e.g. Services) is now generated statically on startup.
  We might as well dump it in a file and import it instead of generating it over and over again.
* But: a lot of HASS-things are still only dynamically generated based on the things that we see,
  like Measurement Units. Unfortunately they're impossible to find without reflection:
    - for all classes in `homeassistant.const`,
  
      - if class name starts with `"UnitOf"`,

        - grab the enums in there?
        
    I guess we'll just have to do it manually...