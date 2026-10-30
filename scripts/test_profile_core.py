"""Profile HTTP verification and browser server, disposable SQLite/storage only."""
import io
import argparse
import re
import sys
import tempfile
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
from sqlalchemy import event
from PIL import Image
from test_settings_core import setup_app, login, ADMIN_PASSWORD, db
from app.models import User, AuthIdentity, UserSession, LoginEvent, PasswordHistory, PasswordResetToken, PasswordlessToken
from app.services import upload_service, google_auth_service, profile_photo_service, passwordless_service, password_reset_service
from werkzeug.datastructures import FileStorage


def image_bytes(kind="PNG"):
    stream = io.BytesIO()
    Image.new("RGB", (180, 180), "#0066ff").save(stream, kind)
    return stream.getvalue()


def run(app, ids):
    client = app.test_client()
    assert client.get("/profile").status_code == 302
    login(client, "core_admin", ADMIN_PASSWORD)
    page = client.get("/profile")
    assert page.status_code == 200
    csrf = re.search(rb'data-csrf="([^"]+)"', page.data)[1].decode()
    headers = {"X-CSRFToken": csrf}
    checks = 0
    def check(label, condition):
        nonlocal checks
        assert condition, label
        checks += 1
        print("PASS:", label)
    check("authenticated current-user values", b"admin@core.test" in page.data and b"core_admin" in page.data)
    check("four tabs, no Seguridad", all(x.encode() in page.data for x in ("Mi perfil", "Preferencias", "Actividad", "Sesiones")) and b">Seguridad<" not in page.data)
    check("branch truthful", b"No disponible" in page.data)
    check("anonymous profile mutation denied", app.test_client().patch('/api/auth/me/profile',json={"name":"X"}).status_code in (400,401))
    with app.app_context():
        for model in (PasswordResetToken, PasswordlessToken):
            db.session.add(model(user_id=ids['admin'], email='admin@core.test', token_hash='old-token', expires_at=datetime.utcnow()+timedelta(hours=1)))
        for provider, subject in [('google','immutable-google-sub'),('local',str(ids['admin'])),('passwordless','admin@core.test')]:
            db.session.add(AuthIdentity(user_id=ids['admin'],provider=provider,provider_user_id=subject,email='admin@core.test'))
        db.session.commit()
    response = client.patch("/api/auth/me/profile", json={"name":"Administrador de prueba", "email":"new@core.test", "phone":"0412-555-1234", "job_title":"Coordinador", "bio":"Perfil de prueba."}, headers=headers)
    check("allowed personal fields saved", response.status_code == 200 and response.json["profile"]["phone"] == "0412-555-1234")
    for key, value in {"username":"changed", "role":"vendedor", "permissions":["roles:manage"], "is_active":False, "user_id":ids["vendedor"], "id":ids["vendedor"], "mfa":True, "email_verified":True, "profile_photo_filename":"evil"}.items():
        response=client.patch("/api/auth/me/profile",json={key:value,"name":"BAD"},headers=headers)
        check("reject protected " + key, response.status_code == 400)
    with app.app_context():
        user=db.session.get(User,ids["admin"])
        check("role/status/username intact", user.role=="admin" and user.is_active and user.username=="core_admin" and user.name=="Administrador de prueba")
        check("other user intact", db.session.get(User,ids["vendedor"]).name=="Vendedor")
        check("changed email unverified", not user.email_verified)
        check("old emailed tokens invalidated", all(model.query.filter_by(token_hash='old-token').one().used_at for model in (PasswordResetToken, PasswordlessToken)))
        check("Google subject preserved", AuthIdentity.query.filter_by(provider='google').one().provider_user_id=='immutable-google-sub')
        check("local email updated and passwordless old binding retired",AuthIdentity.query.filter_by(provider='local').one().email=='new@core.test' and AuthIdentity.query.filter_by(provider='passwordless').count()==0)
        with patch('app.services.auth_flow_service.finish_login',return_value={}):
            google_auth_service.login_from_userinfo({'sub':'immutable-google-sub','email':'admin@core.test','email_verified':True})
        check("Google cannot verify unrelated new email",not user.email_verified)
    check("case insensitive email uniqueness",client.patch('/api/auth/me/profile',json={'email':'VEN@CORE.TEST'},headers=headers).status_code==409)
    for payload in ({"name":""},{"email":"bad"},{"email":"ven@core.test"},{"bio":"x"*301},{"phone":123},[]):
        check("invalid fields rejected",client.patch('/api/auth/me/profile',json=payload,headers=headers).status_code in (400,409))
    check("CSRF required",client.patch('/api/auth/me/profile',json={"name":"BAD"},headers={"X-CSRFToken":"bad"}).status_code==400)
    check("preferences honest", "No hay preferencias personales configurables" in client.get('/profile?tab=preferences').text)
    check("canonical notifications", '/settings?tab=notifications' in page.text)
    check("canonical activity", '/profile?tab=activity' in page.text)
    check("account menu canonical profile", '<a href="/profile" aria-current="page">' in page.text)
    check("account toggle is a button", 'type="button" class="ix-user ix-account-toggle"' in page.text)
    app.debug = False
    check("production ref remains live", 'data-reference="0"' in client.get('/profile?ref=1').text)
    check("production ref update remains live", client.patch('/api/auth/me/profile?ref=1',json={'bio':'Perfil de prueba.'},headers=headers).status_code==200)
    app.debug = True
    check("first ref parameter controls rendering", 'data-reference="0"' in client.get('/profile?ref=0&ref=1').text)
    with app.app_context():
        from app.services.exceptions import ValidationError
        before = AuthIdentity.query.count()
        for verified in (False, None, 'true'):
            try:
                google_auth_service.login_from_userinfo({'sub':'unverified-google','email':'new@core.test','email_verified':verified})
                check('Google unverified rejected',False)
            except ValidationError as error:
                check('Google unverified rejected',error.status_code==401)
        check('Google unverified creates no binding',AuthIdentity.query.count()==before)
        try:
            google_auth_service.login_from_userinfo({'sub':'different-google','email':'new@core.test','email_verified':True})
            check('changed local email cannot link different Google subject',False)
        except ValidationError as error:
            check('changed local email cannot link different Google subject',error.status_code==403)
    with app.app_context():
        db.session.add(LoginEvent(user_id=ids["vendedor"],identifier="core_admin",success=True))
        db.session.add(LoginEvent(user_id=None,identifier="new@core.test",success=False))
        for i in range(12):
            db.session.add(LoginEvent(user_id=ids["admin"],identifier="core_admin",success=i%2==0,device_label="Own browser"))
        db.session.commit()
    history=client.get('/api/settings/security/login-history').json
    check("real paginated login events",history['total']==13 and history['pages']==2 and len(history['items'])==10)
    check("history excludes other user's matching identifier",all(i['device']=="Own browser" for i in history['items']))
    check("activity rendered",'Own browser' in client.get('/profile?tab=activity').text)
    check("activity page 2",'Anterior' in client.get('/profile?tab=activity&page=2').text)
    with app.test_request_context():
        from app.services import login_event_service
        user=db.session.get(User,ids['admin'])
        check("activity status filter",login_event_service.list_events(user,filters={'status':'failed'})['total']==6)
        check("activity device search",login_event_service.list_events(user,filters={'search':'Own browser'})['total']==12)
        check("activity description search",login_event_service.list_events(user,filters={'search':'exitoso'})['total']==7)
        check("activity unsupported type empty",login_event_service.list_events(user,filters={'type':'password'})['total']==0)
        check("activity date bounds",login_event_service.list_events(user,filters={'date_to':'2000-01-01'})['total']==0)
        check("activity invalid date safe",login_event_service.list_events(user,filters={'date_to':'9999-12-31'})['total']==0)
        check("activity date inclusive",login_event_service.list_events(user,filters={'date_from':datetime.utcnow().strftime('%Y-%m-%d'),'date_to':datetime.utcnow().strftime('%Y-%m-%d')})['total']==13)
        check("activity five per page",len(login_event_service.list_events(user,per_page=5)['items'])==5)
        last=login_event_service.list_events(user,page=999,per_page=5,filters={'search':'Own','status':'failed'})
        check('out of range clamps filtered last page',last['page']==2 and last['total']==6 and len(last['items'])==1)
        empty=login_event_service.list_events(user,page=999,filters={'search':'no-matches'})
        check('empty pagination normalized',empty['page']==1 and empty['pages']==0 and empty['total']==0 and not empty['items'])
        check('negative page normalized',login_event_service.list_events(user,page=-1)['page']==1)
        check('single page normalized',login_event_service.list_events(user,page=999,per_page=20)['page']==1)
        check('reversed dates empty',login_event_service.list_events(user,filters={'date_from':'2026-02-02','date_to':'2026-02-01'})['total']==0)
        db.session.add(LoginEvent(user_id=user.id,identifier=user.username,success=False,reason='secret-internal-detail'))
        db.session.commit()
        item=login_event_service.list_events(user)['items'][0]
        check("unknown reason label safe",item['reason_label']=='Intento de inicio de sesión fallido' and 'secret' not in item['description'])
    filtered=client.get('/profile?tab=activity&per_page=5&search=Own&status=failed').text
    check("activity filters persist in pagination",'search=Own' in filtered and 'status=failed' in filtered and 'Mostrando 1 a 5 de 6 registros' in filtered)
    check("activity real columns and only persisted type",all(x in filtered for x in ['Fecha y hora','Descripción','Exitoso','Fallido']) and 'value="password"' not in filtered)
    other=app.test_client();login(other,'core_admin',ADMIN_PASSWORD)
    vendor=app.test_client();login(vendor,'core_ven','ven123')
    sessions=client.get('/api/settings/security/sessions').json
    check("own sessions and current",len(sessions['items'])==2 and sum(s['current'] for s in sessions['items'])==1)
    another=next(s['id'] for s in sessions['items'] if not s['current'])
    foreign=vendor.get('/api/settings/security/sessions').json['current_id']
    check('session revoke CSRF required',client.delete(f'/api/settings/security/sessions/{another}',headers={'X-CSRFToken':'bad'}).status_code==400)
    check("cannot revoke foreign session",client.delete(f'/api/settings/security/sessions/{foreign}',headers=headers).status_code==404)
    check("revoke own other session",client.delete(f'/api/settings/security/sessions/{another}',headers=headers).status_code==200)
    check("revoked cookie rejected",other.get('/api/auth/me').status_code==401)
    login(other,'core_admin',ADMIN_PASSWORD)
    check("revoke all others",client.post('/api/settings/security/sessions/revoke',json={'scope':'others'},headers=headers).json['closed']==1)
    check("current survives",client.get('/api/auth/me').status_code==200)
    def upload(raw,ext='png',mime='image/png',query='',extra=None):
        return client.post('/api/auth/me/profile-photo'+query,data={'image':(io.BytesIO(raw),'../../unsafe.'+ext,mime)},headers=extra or headers)
    for kind,ext,mime in [('PNG','png','image/png'),('JPEG','jpg','image/jpeg'),('WEBP','webp','image/webp')]:
        response=upload(image_bytes(kind),ext,mime)
        check(kind+' valid upload',response.status_code==200)
        with app.app_context():
            name=db.session.get(User,ids['admin']).profile_photo_filename
            files=list(Path(upload_service.folder_path('users')).iterdir())
            check('safe name and replacement',re.fullmatch(r'u[0-9]+_[a-f0-9]{16}\.webp',name) is not None and len(files)==1)
        check('image served',client.get(response.json['profile_photo_url']).status_code==200)
    for raw,ext,mime in [(b'x'*(2*1024*1024+1),'png','image/png'),(b'not-image','png','image/png'),(image_bytes()[:20],'png','image/png'),(b'<svg/>','svg','image/svg+xml'),(image_bytes('JPEG'),'png','image/png')]:
        check('invalid avatar rejected',upload(raw,ext,mime).status_code in (400,413))
    with app.app_context():
        user=db.session.get(User,ids['admin']); old=user.profile_photo_filename
        with patch.object(db.session,'commit',side_effect=RuntimeError('simulated DB failure')):
            try:
                profile_photo_service.set_photo(user,FileStorage(stream=io.BytesIO(image_bytes()),filename='safe.png',content_type='image/png'))
            except RuntimeError:
                pass
        check('avatar rollback preserves old file and removes new',user.profile_photo_filename==old and [p.name for p in Path(upload_service.folder_path('users')).iterdir()]==[old])
        UserSession.query.filter_by(user_id=ids['admin']).update({'last_seen_at':datetime.utcnow()-timedelta(minutes=5)})
        db.session.commit()
    writes=[]
    def capture(conn,cursor,statement,parameters,context,many):
        if statement.lstrip().split()[0].upper() in ('INSERT','UPDATE','DELETE','ALTER','CREATE','DROP'):
            writes.append(statement.split()[0])
    with app.app_context():
        engine=db.engine
    event.listen(engine,'before_cursor_execute',capture)
    try:
        for tab in ('profile','preferences','activity','sessions'):
            check('reference '+tab,client.get('/profile?ref=1&tab='+tab).status_code==200)
        check('repeated reference query zero touch',client.get('/profile?ref=1&ref=0').status_code==200)
        check('repeated reference referrer zero touch',client.get('/api/auth/me',headers={'Referer':'http://localhost/profile?ref=1&ref=0'}).status_code==200)
        for suffix,extra in [('?ref=1',headers),('',{**headers,'Referer':'http://localhost/profile?ref=1'}),('?ref=1&ref=0',headers)]:
            check('reference fields blocked',client.patch('/api/auth/me/profile'+suffix,json={'name':'BAD'},headers=extra).status_code==403)
            check('reference upload blocked',upload(image_bytes(),query=suffix,extra=extra).status_code==403)
            for path,method,payload in [('/api/auth/change-password','post',{}),('/api/auth/logout','post',{}),('/api/auth/me/profile-photo','delete',{}),('/api/settings/security/sessions/revoke','post',{'scope':'others'}),('/api/settings/notifications/read-all','post',{})]:
                check('reference blocked '+path,getattr(client,method)(path+suffix,json=payload,headers=extra).status_code==403)
            check('reference individual session revoke blocked',client.delete(f'/api/settings/security/sessions/{foreign}'+suffix,headers=extra).status_code==403)
        check('REFERENCE WRITES = 0',not writes)
        check('reference Google callback blocked',client.get('/api/auth/google/callback?ref=1').status_code==403)
        check('reference callback writes = 0',not writes)
    finally:
        event.remove(engine,'before_cursor_execute',capture)
    response=client.post('/api/auth/change-password',json={'current_password':ADMIN_PASSWORD,'new_password':'NewProfilePass999','confirm_password':'NewProfilePass999'},headers=headers)
    check('existing password flow',response.status_code==200)
    with app.app_context():
        check('password history reused',PasswordHistory.query.filter_by(user_id=ids['admin']).count()>=1)
    check('avatar removal existing flow',client.delete('/api/auth/me/profile-photo',headers=headers).status_code==200)
    check('initials fallback',b'profile-avatar-initials' in client.get('/profile').data)
    check('non-admin neutral label','Información del usuario' in vendor.get('/profile').text)
    vendor_csrf = re.search(rb'data-csrf="([^"]+)"',vendor.get('/profile').data)[1].decode()
    current_closed=vendor.delete(f'/api/settings/security/sessions/{foreign}',headers={'X-CSRFToken':vendor_csrf})
    check('explicit current session revoke safe',current_closed.status_code==200 and current_closed.json['current_closed'] and vendor.get('/api/auth/me').status_code==401)
    check('canonical logout',client.post('/api/auth/logout').status_code==200 and client.get('/api/auth/me').status_code==401)
    with app.app_context():
        for model in (PasswordResetToken, PasswordlessToken):
            db.session.add(model(user_id=ids['admin'],email='admin@core.test',token_hash=passwordless_service._hash_token('stale-address'),expires_at=datetime.utcnow()+timedelta(hours=1)))
        db.session.commit()
    check('stale address passwordless token denied',client.post('/api/auth/passwordless/verify',json={'email':'admin@core.test','token':'stale-address'}).status_code==401)
    check('stale address reset token denied',client.post('/api/auth/password-reset/confirm',json={'token':'stale-address','new_password':'AnotherValidPass999','confirm_password':'AnotherValidPass999'}).status_code==401)
    with app.test_request_context():
        db.session.add(AuthIdentity(user_id=ids['vendedor'],provider='passwordless',provider_user_id='new@core.test',email='new@core.test'))
        db.session.add(PasswordlessToken(user_id=ids['admin'],email='new@core.test',token_hash=passwordless_service._hash_token('fresh-address'),expires_at=datetime.utcnow()+timedelta(hours=1)))
        db.session.commit()
        with patch('app.services.auth_flow_service.finish_login',return_value={}):
            passwordless_service.verify({'email':'new@core.test','token':'fresh-address'})
        check('verified current address identity follows owner',AuthIdentity.query.filter_by(provider='passwordless',provider_user_id='new@core.test').one().user_id==ids['admin'])
        local = User(name='Cuenta local', email='local-google@core.test', username='local_google', role='vendedor')
        local.set_password('LocalTestPass999')
        db.session.add(local); db.session.commit()
        try:
            google_auth_service.login_from_userinfo({'sub':'local-google-sub','email':local.email,'email_verified':True})
            check('unverified local address cannot auto-link Google',False)
        except ValidationError as error:
            check('unverified local address cannot auto-link Google',error.status_code==403)
        local.email_verified = True; db.session.commit()
        with patch('app.services.auth_flow_service.finish_login',return_value={}):
            linked, action, _ = google_auth_service.login_from_userinfo({'sub':'local-google-sub','email':local.email,'email_verified':True})
            check('verified local account can link Google',linked.id==local.id and action=='linked')
            created, action, _ = google_auth_service.login_from_userinfo({'sub':'new-google-sub','email':'google-new@core.test','email_verified':True})
            check('new verified Google account remains supported',action=='created' and created.role=='vendedor' and created.email_verified)
    from migrate_user_profile import migrate, COLUMNS
    connection=MagicMock(); cursor=connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.side_effect=[(0,),(0,),(0,),(1,),(1,),(1,)]
    migrate(connection,'disposable_schema'); migrate(connection,'disposable_schema')
    statements=[call.args[0] for call in cursor.execute.call_args_list]
    alters=[sql for sql in statements if sql.startswith('ALTER')]
    check('migration adds exactly allowed columns once',alters==[f'ALTER TABLE users ADD COLUMN {name} {definition}' for name,definition in COLUMNS.items()])
    check('migration idempotent and no destructive SQL',len(statements)==9 and not any(re.search(r'\b(DROP|DELETE|TRUNCATE|UPDATE)\b',sql) for sql in statements))
    print(f'{checks} profile checks passed. Valuable DB writes: 0.')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=5041)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='conejo-profile-') as tmp:
        app,ids=setup_app(Path(tmp))
        app.debug=True
        with patch.object(upload_service,'uploads_root',return_value=str(Path(tmp)/'uploads')):
            if args.serve:
                @app.get('/__profile_test__')
                def isolated_marker():
                    return {'isolated': True, 'database': 'temporary-sqlite'}
                with app.app_context():
                    for index in range(24):
                        db.session.add(LoginEvent(user_id=ids['admin'], identifier='core_admin',
                            success=index % 2 == 0, device_label='Navegador de prueba',
                            ip_address='127.0.0.1', created_at=datetime.utcnow()-timedelta(hours=index)))
                    db.session.commit()
                app.run(host='127.0.0.1',port=args.port,use_reloader=False,debug=True,use_debugger=False)
            else:
                try:
                    run(app,ids)
                finally:
                    with app.app_context():
                        db.session.remove()
                        db.engine.dispose()
