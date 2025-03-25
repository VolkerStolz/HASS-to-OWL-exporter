# from oauthlib.oauth2 import MissingTokenError
import argparse
from requests_oauthlib import OAuth2Session
from flask import Flask, redirect, request, session, url_for, render_template, flash
from flask.logging import default_handler
# from flask_indieauth import requires_indieauth
import logging
from logging.config import dictConfig
import urllib.parse
import os

import hacvt
from ConfigSource import ConfigSource


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
            client_id = request.url_root
            oa = OAuth2Session(client_id)
            oa.headers['User-Agent'] = "vs/1.0"
            authorization_base_url = urllib.parse.urljoin(url, f'/auth/authorize?redirect_uri={client_id}/callback')
            authorization_url, state = oa.authorization_url(authorization_base_url)
            # State is used to prevent CSRF, keep this for later.
            session['oauth_state'] = state
            session['url'] = url
            if 'privacy' in request.form:
                session['privacy'] = request.form['privacy']
            else:
                session['privacy'] = None
            return redirect(authorization_url)
    return render_template('index.html')


@app.route("/callback", methods=["GET"])
def callback():
    client_id = request.url_root
    oa = OAuth2Session(client_id, state=session['oauth_state'])
    oa.headers['User-Agent'] = "vs/1.0"
    ha_code = request.args.get('code')
    app.logger.warning(ha_code)
    app.logger.warning(request.args)
    # token = oa.token_from_fragment(authorization_response=request.url, code=ha_code)
    token_url = urllib.parse.urljoin(session['url'], '/auth/token')
    token = oa.fetch_token(token_url, authorization_response=request.url, code=ha_code, include_client_id=True)
    session['oauth_token'] = token

    # TODO: why redirect, why not inline...
    return redirect(url_for('status'))


@app.route("/status", methods=["GET"])
def status():
    t = session['oauth_token']
    assert 'access_token' in t
    privacy_option = session['privacy']
    # We yolo and hope that we finish before expiry.
    api_url = urllib.parse.urljoin(session['url'], '/api/')
    cs = ConfigSource(api_url, t['access_token'])
    tool = hacvt.HACVT(cs)
    # Bad UX since we'll get stuck here.
    # TODO: Use some kind of progress indicator and send this in the background.
    g = tool.main(cs, privacy=privacy_option)
    # used in template
    ha_data = g.serialize(format='turtle')

    # Show data to user if they want us to keep it.
    return render_template('submit.html', ha_data=ha_data)


@app.route("/submit", methods=["POST"])
def submit():
    app.logger.warning("done")
    # TODO


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    app.secret_key = os.urandom(24)
    # os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.logger.setLevel(logging.DEBUG)
    app.run(debug=True)
