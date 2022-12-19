import csv
from functools import cache


@cache
def parseServices(fname='services.csv'):
    out = {}
    with open(fname, newline='') as f:
        i = csv.reader(f, delimiter=':')
        for a, b in i:
            if b == "":  # no services?
                out[a] = []
            else:
                out[a] = b.split(',')
    return out


def mkServiceToDomainTable():
    hass_svcs = {}
    for component_name, svc_services in parseServices().items():
        for s in svc_services:
            if s in hass_svcs:
                t = hass_svcs[s]
                t.add(component_name)
                hass_svcs[s] = t
            else:
                hass_svcs[s] = set([component_name])
    return hass_svcs
