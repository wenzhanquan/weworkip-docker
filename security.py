"""Password-free panel, as requested; retain cross-site browser request checks."""
from urllib.parse import urlsplit
from flask import request,redirect

def install_security(app,data_dir):
    app.config['MAX_CONTENT_LENGTH']=65536

    @app.before_request
    def guard():
        origin=request.headers.get('Origin')
        if origin and urlsplit(origin).netloc!=request.host:return 'Origin rejected',403
        if request.headers.get('Sec-Fetch-Site')=='cross-site':return 'Cross-site request rejected',403

    @app.get('/auth-check')
    def auth_check():return '',200

    @app.route('/login',methods=['GET','POST'])
    def login():return redirect('/')
