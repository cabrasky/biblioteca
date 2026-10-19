# -*- coding: utf-8 -*-

#  This file is part of the Calibre-Web (https://github.com/janeczku/calibre-web)
#
#  This program is free software: you can redistribute it and/or modify
#  it under the terms of the GNU General Public License as published by
#  the Free Software Foundation, either version 3 of the License, or
#  (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program. If not, see <http://www.gnu.org/licenses/>.

"""[biblioteca] Alta automática de usuarios autenticados por el proxy (Authelia/FreeIPA).

Solo actúa si la autenticación por cabecera del proxy está activada en la configuración
(y por tanto la petición viene de una IP de confianza) y además se activa por entorno:

  CALIBRE_WEB_PROXY_AUTOCREATE=1          crea el usuario la primera vez que entra
  CALIBRE_WEB_PROXY_GROUPS_HEADER=Remote-Groups
  CALIBRE_WEB_PROXY_EMAIL_HEADER=Remote-Email
  CALIBRE_WEB_PROXY_ADMIN_GROUP=admins    miembros -> rol de administrador (se sincroniza)
  CALIBRE_WEB_PROXY_ADMIN_USERS=cabrasky  usuarios administradores aunque no estén en el grupo

Los usuarios nuevos reciben los roles, la visibilidad y el idioma por defecto que se
configuran en «Configuración de la interfaz» del panel de administración. La contraseña
local es aleatoria: el acceso es siempre a través del proxy.
"""

import os
import re
import secrets

from sqlalchemy.exc import IntegrityError, OperationalError
from werkzeug.security import generate_password_hash

from . import ub, config, constants, logger

log = logger.create()

NAME_RE = re.compile(r"^[A-Za-z0-9._@-]{1,64}$")


def _env(name, default=""):
    return os.environ.get(name, default).strip()


def enabled():
    return _env("CALIBRE_WEB_PROXY_AUTOCREATE") in ("1", "true", "yes", "on")


def _is_admin(username, headers):
    admin_users = {u.strip().lower() for u in _env("CALIBRE_WEB_PROXY_ADMIN_USERS").split(",") if u.strip()}
    if username.lower() in admin_users:
        return True
    group = _env("CALIBRE_WEB_PROXY_ADMIN_GROUP", "admins")
    header = _env("CALIBRE_WEB_PROXY_GROUPS_HEADER", "Remote-Groups")
    groups = {g.strip() for g in (headers.get(header) or "").split(",") if g.strip()}
    return bool(group) and group in groups


def _email(username, headers):
    header = _env("CALIBRE_WEB_PROXY_EMAIL_HEADER", "Remote-Email")
    email = (headers.get(header) or "").strip()
    if email and "@" in email and len(email) <= 120:
        if not ub.session.query(ub.User).filter(ub.User.email == email).first():
            return email
    return "{}@proxy.invalid".format(username.lower())


def sync_user(user, username, headers):
    """Devuelve el usuario (creándolo si hace falta) con el rol de admin sincronizado."""
    if not enabled():
        return user
    if user is None:
        if not NAME_RE.match(username) or username.lower() == "guest":
            log.warning("Proxy auth: nombre de usuario no válido para alta automática: %r", username)
            return None
        user = ub.User()
        user.name = username
        user.email = _email(username, headers)
        user.password = generate_password_hash(secrets.token_urlsafe(32))
        user.role = config.config_default_role
        user.sidebar_view = config.config_default_show
        user.locale = config.config_default_locale
        user.default_language = config.config_default_language
        user.allowed_tags = config.config_allowed_tags
        user.denied_tags = config.config_denied_tags
        user.allowed_column_value = config.config_allowed_column_value
        user.denied_column_value = config.config_denied_column_value
        if _is_admin(username, headers):
            user.role = constants.ADMIN_USER_ROLES
            user.sidebar_view = constants.ADMIN_USER_SIDEBAR
        ub.session.add(user)
        try:
            ub.session.commit()
            log.info("Proxy auth: usuario '%s' creado (admin=%s)", username, user.role_admin())
        except (IntegrityError, OperationalError) as ex:
            ub.session.rollback()
            log.error("Proxy auth: no se pudo crear el usuario '%s': %s", username, ex)
            return ub.session.query(ub.User).filter(ub.User.name == username).first()
        return user

    # Usuario existente: sincronizar solo el bit de administrador con el grupo del proxy
    want_admin = _is_admin(username, headers)
    if want_admin != bool(user.role_admin()):
        if want_admin:
            user.role = (user.role or 0) | constants.ADMIN_USER_ROLES
        else:
            user.role = (user.role or 0) & ~constants.ROLE_ADMIN
        try:
            ub.session.commit()
            log.info("Proxy auth: rol de admin de '%s' -> %s", username, want_admin)
        except OperationalError as ex:
            ub.session.rollback()
            log.error("Proxy auth: no se pudo actualizar '%s': %s", username, ex)
    return user
