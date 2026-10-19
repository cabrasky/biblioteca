// biblioteca — fork de Calibre-Web (janeczku/calibre-web) + despliegue del homelab.
// Jenkins SOLO construye y publica las imágenes (Calibre-Web + pedidos).
// Argo CD (app biblioteca) despliega desde Git; Image Updater detecta los nuevos digest.
// El registry local (registry.cabrasky.net -> nginx .21 -> .26:5000) no requiere login.

pipeline {
  // En el controlador (tiene el socket de Docker montado); el agente ai-server-16 no tiene acceso a Docker.
  agent { label 'built-in' }

  options {
    disableConcurrentBuilds()
    buildDiscarder(logRotator(numToKeepStr: '15'))
  }

  environment {
    CW_IMAGE = 'registry.cabrasky.net/biblioteca-calibre-web'
    PEDIDOS_IMAGE = 'registry.cabrasky.net/biblioteca-pedidos'
  }

  stages {
    stage('Cambios') {
      steps {
        script {
          // Los commits de Argo CD Image Updater solo tocan k8s/: no reconstruir (evita bucle build→digest→commit).
          // status 0 = hay algún fichero fuera de k8s/ (o no se puede calcular el diff) -> construir
          def code = sh(script: "{ git diff --name-only HEAD~1 HEAD 2>/dev/null || echo FORCE; } | grep -qv '^k8s/'", returnStatus: true)
          env.SKIP_BUILD = (code == 0) ? 'false' : 'true'
          if (env.SKIP_BUILD == 'true') {
            currentBuild.result = 'NOT_BUILT'
            echo 'Solo cambios en k8s/ (commit de Image Updater): no se construye imagen'
          }
        }
      }
    }

    stage('Setup Docker CLI') {
      when { expression { env.SKIP_BUILD != 'true' } }
      steps {
        sh '''
          if ! command -v docker >/dev/null; then
            curl -fsSL https://download.docker.com/linux/static/stable/x86_64/docker-27.5.1.tgz -o /tmp/docker.tgz
            tar xzf /tmp/docker.tgz -C /tmp && cp /tmp/docker/docker /usr/local/bin/docker && rm -rf /tmp/docker /tmp/docker.tgz
          fi
          docker version --format '{{.Server.Version}}'
        '''
      }
    }

    stage('Build') {
      when { expression { env.SKIP_BUILD != 'true' } }
      steps {
        script { env.TAG = sh(script: 'git rev-parse --short=10 HEAD', returnStdout: true).trim() }
        sh 'docker build --pull -t ${CW_IMAGE}:${TAG} -t ${CW_IMAGE}:latest .'
        sh 'docker build --pull -t ${PEDIDOS_IMAGE}:${TAG} -t ${PEDIDOS_IMAGE}:latest pedidos'
      }
    }

    stage('Smoke test') {
      when { expression { env.SKIP_BUILD != 'true' } }
      steps {
        sh '''
          C=$(docker run -d --rm -e CW_PROXIES_CONFIANZA=127.0.0.1 -e CALIBRE_WEB_PROXY_AUTOCREATE=1 \
                -e CALIBRE_WEB_PROXY_ADMIN_USERS=cabrasky -e CALIBRE_WEB_PETICIONES_API=/pedidos/api ${CW_IMAGE}:${TAG})
          P=$(docker run -d --rm -e ESTADO=/tmp/estado -e INCOMING=/tmp/in ${PEDIDOS_IMAGE}:${TAG})
          trap "docker stop $C $P >/dev/null" EXIT
          PY='import urllib.request as u,sys
h={"Remote-User":sys.argv[2]} if len(sys.argv)>2 else {}
try:
  r=u.urlopen(u.Request("http://127.0.0.1:"+sys.argv[1],headers=h),timeout=5);print(r.status,r.geturl());sys.stdout.write(r.read().decode())
except Exception as e: print("ERR",e)'
          # el primer arranque crea la config con una instancia temporal en 127.0.0.1; esperar a la definitiva
          for i in $(seq 1 90); do docker logs $C 2>&1 | grep -q "Tornado server on 0.0.0.0" && break; sleep 2; done
          for i in $(seq 1 30); do docker exec $C python3 -c "$PY" 8083/ | grep -q "^200" && break; sleep 1; done
          # sin cabecera del proxy -> pantalla de login; con ella -> usuario creado, en español, con la pestaña Peticiones
          docker exec $C python3 -c "$PY" 8083/ | head -1 | grep -q "/login"
          docker exec $C python3 -c "$PY" 8083/ cabrasky > /tmp/cw.html
          grep -q 'nav_peticiones' /tmp/cw.html
          grep -q 'id="top_admin"' /tmp/cw.html
          grep -q 'Cerrar sesión' /tmp/cw.html
          docker exec $C python3 -c "$PY" 8083/peticiones otro | grep -q 'data-api="/pedidos/api"'
          if docker exec $C python3 -c "$PY" 8083/ otro | grep -q 'id="top_admin"'; then echo "un usuario normal es admin"; exit 1; fi
          # el importador mete libros en la biblioteca con calibredb
          docker exec $C sh -c 'mkdir -p /tmp/in /tmp/e && printf "Hola\n" > /tmp/t.txt && ebook-convert /tmp/t.txt "/tmp/in/Prueba.epub" >/dev/null && INCOMING=/tmp/in ESTADO=/tmp/e REPOSO=1 INTERVALO=1 timeout 8 python3 /usr/local/bin/importador.py' | grep -q 'importado Prueba.epub'
          PYP='import http.client as h,sys;c=h.HTTPConnection("127.0.0.1",8000,timeout=3);c.request("GET",sys.argv[1]);print(c.getresponse().status)'
          for i in $(seq 1 30); do [ "$(docker exec $P python -c "$PYP" /pedidos/healthz 2>/dev/null)" = 200 ] && break; sleep 1; done
          test "$(docker exec $P python -c "$PYP" /pedidos/healthz)" = 200
          # la UI de pedidos ahora es la pestaña de Calibre-Web
          test "$(docker exec $P python -c "$PYP" /pedidos/)" = 302
          # sin cabecera Remote-User (Authelia) la API responde 401
          test "$(docker exec $P python -c "$PYP" /pedidos/api/pedidos)" = 401
        '''
      }
    }

    stage('Push') {
      when { expression { env.SKIP_BUILD != 'true' } }
      steps {
        sh '''
          for img in ${CW_IMAGE} ${PEDIDOS_IMAGE}; do
            docker push $img:${TAG}
            docker push $img:latest
          done
        '''
      }
    }
  }

  post {
    success { script { if (env.SKIP_BUILD != 'true') echo "✅ ${CW_IMAGE}:${TAG} y ${PEDIDOS_IMAGE}:${TAG} publicadas — Argo CD desplegará biblioteca" } }
    failure { echo '❌ Falló el pipeline de biblioteca' }
  }
}
