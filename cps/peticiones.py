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

"""[biblioteca] Pestaña «Peticiones»: pedir libros que aún no están en la biblioteca.

La página vive dentro de Calibre-Web (mismo menú, tema y sesión). La búsqueda y la descarga
las hace un servicio aparte (API REST en CALIBRE_WEB_PETICIONES_API, por defecto /pedidos/api,
mismo dominio); cuando el libro termina de descargarse se importa solo en la biblioteca.
La pestaña solo aparece si CALIBRE_WEB_PETICIONES_API está definida.
"""

import os

from flask import Blueprint, abort

from .cw_login import current_user
from .render_template import render_title_template
from .usermanagement import user_login_required

peticiones = Blueprint('peticiones', __name__)


def api_url():
    return os.environ.get("CALIBRE_WEB_PETICIONES_API", "").strip().rstrip("/")


def enabled():
    return bool(api_url())


@peticiones.route("/peticiones")
@user_login_required
def index(data=None):
    if not enabled() or current_user.is_anonymous:
        abort(404)
    return render_title_template("peticiones.html", title="Peticiones", page="peticiones",
                                 peticiones_api=api_url())
