import json
from jsonpath_ng.ext import parse
import os
from rdflib import Literal, Graph, URIRef
from rdflib.namespace import Namespace, RDF, OWL
from urllib.parse import quote
import sys


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def mkname(name):
    return name.replace(" ", "_").replace("/", "_")


def getNameOrOrig(thing):
    if e['name'] is not None:
        return e['name']
    else:
        return e['original_name']


f_d = open(os.path.expanduser("~/tmp/core.device_registry"))
f_e = open(os.path.expanduser("~/tmp/core.entity_registry"))

j_d = json.load(f_d)
j_e = json.load(f_e)

my_devs = {}
my_ents = {}

# TODOs
# - escape "/" in names!

for e in j_e['data']['entities']:
    e_id = e['entity_id']
    e_did = e['device_id']
    # let's create the owning device:
    if e_did is not None:
        jpath = parse('$.data.devices[?id=="' + e_did + '"]')
        jmatch = [match.value for match in jpath.find(j_d)]
        if len(jmatch) != 1:
            raise "That didn't work for:" + e_did
        d = jmatch[0]
        idx = e_did
    else:
        idx = e['id']
        d = {}  # XXX! Hack.
        # We expect devices to have a name later on. Quite a chain of fallbacks here:
        if e['name'] is not None:
            d['name'] = e['name']
        else:
            d['name'] = e['original_name']
        if d['name'] is None:
            d['name'] = e['entity_id']
        assert d['name'] is not None, str(e)
    if not idx in my_devs:
        my_devs[idx] = d
        # plural names:
        my_devs[idx]['sensors'] = []
        my_devs[idx]['switches'] = []
        my_devs[idx]['hvacs'] = []
        eprint("Added " + str(idx))
    # ...and then directly add each sensor or switch:
    if e_id.startswith("sensor.") or e_id.startswith("binary_sensor."):
        my_devs[idx]['sensors'].append(e)
    elif e_id.startswith("switch."):
        my_devs[idx]['switches'].append(e)
    elif e_id.startswith("climate."):
        # In my case device_id is null!
        # I guess we add it as a device then...
        # TODO: probably should do this in general?

        my_devs[idx]['hvacs'].append(e)
    else:
        eprint(f"Skipping device {e_id}.\n")
        continue

g = Graph(bind_namespaces="core")
SAREF = Namespace("https://saref.etsi.org/core/")
g.bind("saref", SAREF)
g.bind("owl", OWL)
saref_import = URIRef("http://my.name.spc")
g.add((saref_import, RDF.type, OWL.Ontology))
g.add((saref_import, OWL.imports, URIRef(str(SAREF))))

# print(str(my_devs))

for (id, d) in my_devs.items():
    # TODO: why _g...?
    d_g = URIRef("http://my.name.spc/" + mkname(d['name']))
    d_cl = "Device"  # to be overriden below
    if 'manufacturer' in d:
        g.add((d_g, SAREF['hasManufacturer'], Literal(d['manufacturer'])))
    # TODO: use some kind of lookup-table
    cl = "Switch"
    for s in d['switches']:
        e_g = URIRef("http://my.name.spc/" + mkname(s['name']))
        g.add((e_g, RDF.type, SAREF['OnOffFunction']))
        g.add((d_g, SAREF['hasFunction'], e_g))
    if len(d['switches']) > 0:
        g.add((d_g, RDF.type, SAREF[cl]))

    # Only assign one class? [TODO]
    d_cl = "Sensor"
    for s in d['sensors']:
        # update class?
        # XXX Bad idea is a Sensor provides multiple values?!
        if s['original_device_class'] == "temperature":
            d_cl = "TemperatureSensor"
        e_name = s['name']
        # TODO: same safety-net for switches?
        if e_name is None:
            e_name = s['original_name']
            if e_name is None:
                e_name = s['entity_id']
        assert e_name is not None, str(s)
        e_m = URIRef("http://my.name.spc/measure/" + mkname(e_name) + "_" + s['id'][0:3])  # TODO: id?
        g.add((e_m, RDF.type, SAREF['Measurement']))
        g.add((d_g, SAREF['makesMeasurement'], e_m))

        e_prop = URIRef("http://my.name.spc/property/" + mkname(e_name))
        # I want my multi-level modelling back...
        unit = None
        if s['original_device_class'] == "temperature":
            unit = URIRef("http://my.name.spc/unit/" + mkname(s['unit_of_measurement']))
            # TODO: I think we're adding dupes here?
            g.add((unit, RDF.type, SAREF['TemperatureUnit']))
            cl = SAREF['Temperature']
        elif s['original_device_class'] == "current":
            unit = URIRef("http://my.name.spc/unit/" + mkname(s['unit_of_measurement']))
            g.add((unit, RDF.type, SAREF['PowerUnit']))
            cl = SAREF['Power']
        elif s['original_device_class'] == "humidity":
            unit = URIRef("http://my.name.spc/unit/" + mkname(s['unit_of_measurement']))
            # LOL, in SAREF, Power has PowerUnit, but Humidity doesn't have a unit...
            g.add((unit, RDF.type, SAREF['UnitOfMeasure']))
            cl = SAREF['Humidity']
        else:
            cl = SAREF['Property']
        # assert cl subclassof Property
        g.add((e_prop, RDF.type, cl))
        if unit is not None:
            g.add((e_m, SAREF['isMeasuredIn'], unit))
        g.add((d_g, SAREF['measuresProperty'], e_prop))
        g.add((e_m, SAREF['relatesToProperty'], e_prop))  # inverse inferred in Protégé
    # At least one sensor?
    if len(d['sensors']) > 0:
        g.add((d_g, RDF.type, SAREF["Sensor"]))

    # treat those special for now
    for s in d['hvacs']:
        e_name = s['name']
        # TODO: same safetynet for switches?
        if e_name is None:
            e_name = s['original_name']
            e_m = URIRef("http://my.name.spc/" + mkname(e_name) + "_" + s['id'][0:3])
        g.add((e_m, RDF.type, SAREF['HVAC']))

    # create appropriate class (see XXX above!)
    # Should probably be list of classes, or let `g.add()` handle redundant decls?
    g.add((d_g, RDF.type, SAREF[d_cl]))

f_out = open("/Users/vs/ha.ttl","w")
print(g.serialize(format='turtle'), file=f_out)

