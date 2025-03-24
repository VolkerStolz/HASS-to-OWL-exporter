# from oauthlib.oauth2 import MissingTokenError
from requests_oauthlib import OAuth2Session
from flask import Flask, redirect, request, session, url_for, g, current_app, jsonify, render_template, flash
from flask.logging import default_handler
# from flask_indieauth import requires_indieauth
import logging
from logging.config import dictConfig
import urllib.parse
import os

import hacvt
from ConfigSource import ConfigSource

flask_port = 5001
client_id = "http://127.0.0.1:"+str(flask_port)

ha_url = 'https://mh30.foldr.org:8123'


dictConfig({
    'version': 1,
    'formatters': {'default': {
        'format': '[%(asctime)s] %(levelname)s in %(module)s: %(message)s',
    }},
    'handlers': {'wsgi': {
        'class': 'logging.StreamHandler',
        'stream': 'ext://flask.logging.wsgi_errors_stream',
        'formatter': 'default'
    }},
    'root': {
        'level': 'INFO',
        'handlers': ['wsgi']
    }
})
app = Flask(__name__)


@app.route("/", methods=['GET', 'POST'])
def index():
    if request.method == 'POST':
        url = request.form['url']
        if not url:
            flash('URL is required!')
        else:
            oa = OAuth2Session(client_id)
            oa.headers['User-Agent'] = "vs/1.0"
            authorization_base_url = urllib.parse.urljoin(url, f'/auth/authorize?redirect_uri={client_id}/callback')
            authorization_url, state = oa.authorization_url(authorization_base_url)
            # State is used to prevent CSRF, keep this for later.
            session['oauth_state'] = state
            session['url'] = url
            return redirect(authorization_url)
    return render_template('index.html')


@app.route("/callback", methods=["GET"])
def callback():
    oa = OAuth2Session(client_id, state=session['oauth_state'])
    oa.headers['User-Agent'] = "vs/1.0"
    ha_code = request.args.get('code')
    app.logger.warning(ha_code)
    app.logger.warning(request.args)
    # token = oa.token_from_fragment(authorization_response=request.url, code=ha_code)
    token_url = urllib.parse.urljoin(session['url'], '/auth/token')
    token = oa.fetch_token(token_url, authorization_response=request.url, code=ha_code, include_client_id=True)
    session['oauth_token'] = token

    return redirect(url_for('status'))


@app.route("/status", methods=["GET"])
def status():
    t = session['oauth_token']
    assert 'access_token' in t
    # We yolo and hope that we finish before expiry.
    cs = ConfigSource(ha_url+"/api/", t['access_token'])
    tool = hacvt.HACVT(cs)
    g = tool.main(cs)
    session['ha_data'] = g.serialize(format='turtle')

    # Show data to user if they want us to keep it.
    return render_template('submit.html')


@app.route("/submit", methods=["POST"])
def submit():
    app.logger.warning("done")


if __name__ == "__main__":
    app.secret_key = os.urandom(24)
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.logger.setLevel(logging.DEBUG)
    app.run(debug=True, port=flask_port)
