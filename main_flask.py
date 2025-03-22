# from oauthlib.oauth2 import MissingTokenError
from requests_oauthlib import OAuth2Session
from flask import Flask, redirect, request, session, url_for, g, current_app, jsonify
from flask.logging import default_handler
# from flask_indieauth import requires_indieauth
import logging
from logging.config import dictConfig
import os

import hacvt
from ConfigSource import ConfigSource

flask_port = 5001
client_id = "http://127.0.0.1:"+str(flask_port)

ha_url = 'https://mh30.foldr.org:8123'
authorization_base_url = f'https://mh30.foldr.org:8123/auth/authorize?redirect_uri=http://127.0.0.1:{flask_port}/callback'
token_url = 'https://mh30.foldr.org:8123/auth/token'

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


@app.route("/")
def demo():
    """Step 1: User Authorization.

    Redirect the user/resource owner to the OAuth provider (i.e. Github)
    using an URL with a few key OAuth parameters.
    """
    oa = OAuth2Session(client_id)
    oa.headers['User-Agent'] = "vs/1.0"
    authorization_url, state = oa.authorization_url(authorization_base_url)
    # State is used to prevent CSRF, keep this for later.
    session['oauth_state'] = state
    app.logger.info("this")
    return redirect(authorization_url)


@app.route("/callback", methods=["GET"])
def callback():
    """ Step 3: Retrieving an access token.

    The user has been redirected back from the provider to your registered
    callback URL. With this redirection comes an authorization code included
    in the redirect URL. We will use that to obtain an access token.
    """

    oa = OAuth2Session(client_id, state=session['oauth_state'])
    oa.headers['User-Agent'] = "vs/1.0"
    ha_code = request.args.get('code')
    app.logger.warning(ha_code)
    app.logger.warning(request.args)
    # token = oa.token_from_fragment(authorization_response=request.url, code=ha_code)
    token = oa.fetch_token(token_url, authorization_response=request.url, code=ha_code, include_client_id=True )
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
    return g.serialize(format='turtle')
    #
    # oa = OAuth2Session(client_id, token=session['oauth_token'])
    # oa.headers['User-Agent'] = "vs/1.0"
    # res = oa.get(ha_url+"/api/error_log")
    # assert res.status_code == 200
    # app.logger.debug(res)
    # return jsonify(res.json())


if __name__ == "__main__":
    app.secret_key = os.urandom(24)
    os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    app.logger.setLevel(logging.DEBUG)
    app.run(debug=True, port=flask_port)
