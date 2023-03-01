# Home-Assistant to OWL Exporter

This tool uses the API of a [Home Assistant](https://www.home-assistant.io) installation to export the static structure of your smart home as an RDF ontology file. It exports the devices, their entities and attributes and locations -- not the current sensor values! You can then browse and query the generated ontology, see **Instructions** below.

The current schema for the types provided by Home Assistant is published at https://www.foldr.org/profiles/homeassistant/. It uses the [SAREF Smart Applications REFerence ontology](https://saref.etsi.org/core/).

This project is ongoing work in HVL's "Smart Software Systems (S3)" project. 

Contact: Volker Stolz (HVL).
Contributions by: [Eduard Kamburjan](https://github.com/Edkamb), [Fernando Macías](https://github.com/femaciasg), [Adam Cheng](https://github.com/adamchengtkc)

# Instructions

0. Ignore the `Makefile`
1. Set up a `venv` and `pip install -r requirements.txt`
2. Run `hacvt.py` with options of your choice (needs at least URL and variable that holds [your long-lived access token](https://developers.home-assistant.io/docs/auth_api/#long-lived-access-token))
3. Grab generated file (`ha.ttl` by default) and import e.g. into [Protégé](https://protege.stanford.edu).

```
$ python hacvt.py -h
usage: hacvt.py [-h] [-d [DEBUG]] [-o OUT] [-p [platform* ...]] [-m 192.0.2.1] [-c ca.crt] url TOKENVAR

positional arguments:
  url                   Full path to API, e.g. https://homeassistant.local:8123/api/.
  TOKENVAR              Name of environment variable where you keep your long-lived access token. NOT THE LITERAL TOKEN!

options:
  -h, --help            show this help message and exit
  -d [DEBUG], --debug [DEBUG]
                        Set Python log level. INFO if not set, otherwise DEBUG or your value here is used.
  -o OUT, --out OUT     Set output filename; `ha.ttl` by default.
  -p [platform* ...], --privacy [platform* ...]
                        Enable privacy filter. `-p` gives a sensible default, otherwise use `-p person zone ...` to specify whitelist -- any other
                        entities NOT in the filter will have their name replaced.
  -m 192.0.2.1, --mount 192.0.2.1
                        Use ForcedIPHTTPSAdapter to override IP for URL; useful on internal IPs.
  -c ca.crt, --certificate ca.crt
                        Path to a CA certificate to validate your https-connection if needed. The string "None" will disable validation.
$ export TOKEN=zzzaaaxxx...
$ python hacvt.py https://homeassistant.local:8123/api/ TOKEN
<lots of RDF output here and in the outputfile>
```
