# from oauthlib.oauth2 import MissingTokenError
import argparse

from celery import shared_task, Celery, Task
from celery.result import AsyncResult
from requests_oauthlib import OAuth2Session
from flask import Flask, redirect, request, session, url_for, render_template, flash, Blueprint, current_app
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

def celery_init_app(app: Flask) -> Celery:
    class FlaskTask(Task):
        def __call__(self, *args: object, **kwargs: object) -> object:
            with app.app_context():
                return self.run(*args, **kwargs)

    celery_app = Celery(app.name, task_cls=FlaskTask)
    celery_app.config_from_object(app.config["CELERY"])
    celery_app.set_default()
    app.extensions["celery"] = celery_app
    return celery_app

def create_app() -> Flask:
    myapp = Flask(__name__)
    myapp.config.from_mapping(
        CELERY=dict(
            broker_url="redis://localhost",
            result_backend="redis://localhost",
            task_ignore_result=True,
        ),
    )
    celery_app = celery_init_app(myapp)
    myapp.register_blueprint(app)
    myapp.secret_key = os.urandom(24)
    # os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
    myapp.logger.setLevel(logging.DEBUG)
    return myapp


app = Blueprint('app', __name__, template_folder='templates')


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
    return redirect(url_for('app.status'))


@shared_task(ignore_result=False)
def traverse_ha(url, token, privacy_option):
    cs = ConfigSource(url, token)
    tool = hacvt.HACVT(cs)
    g = tool.main(cs, privacy=privacy_option)
    ha_data = g.serialize(format='turtle')
    return ha_data


@app.route("/status", methods=["GET"])
def status():
    t = session['oauth_token']
    assert 'access_token' in t
    privacy_option = session['privacy']
    # We yolo and hope that we finish before expiry.
    api_url = urllib.parse.urljoin(session['url'], '/api/')
    result = traverse_ha.delay(api_url, t['access_token'], privacy_option)
    # TODO: Use some kind of progress indicator and send this in the background.
    # used in template

    # Show data to user if they want us to keep it.
    return render_template('processing.html', rid=result.id)


@app.route('/task/<rid>')
def task(rid):
    result = AsyncResult(rid)
    if result.ready():
        data = result.result
        result.forget()
        return render_template('submit.html', ha_data=data)
    else:
        return "not ready :-( Just hit reload... Or are you looking at an old task from long ago that has already expired?"

@app.route("/submit", methods=["POST"])
def submit():
    current_app.logger.warning("done")
    # TODO


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    args = parser.parse_args()
    app = create_app()
    app.run(debug=True)
