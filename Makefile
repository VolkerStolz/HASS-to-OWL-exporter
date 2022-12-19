idx:	core
	mkdir -p idx
	find core/homeassistant/components/ -depth 2 -name __init__.py | gsed -nr 's!.*//(\w*)/.*!egrep -oE "\\bSERVICE_\\w+" core/homeassistant/components/\1/__init__.py | uniq >idx/\1 !p' | sh - 
	find idx -size 0 -exec bash -c 'echo -n "," >{}' \;

services.csv:	idx
	(cd idx && for i in *; do /bin/echo -n "$$i:" && tr '\n' ','< $$i | sed 's/,$$//' ; done) >$@
