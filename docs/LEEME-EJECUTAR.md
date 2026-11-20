# Cómo ejecutar Pokalatro

Solo hace falta **Python 3.8 o superior**. No usa ninguna librería externa, así que no
hay nada que instalar.

Comprobar que Python está:

```powershell
python --version
```

## 1. Probar que el bot está sano (1-2 minutos)

Desde la carpeta del repositorio:

```powershell
python -m unittest discover -s tests -v
```

Tienen que decir `ok` las **30 pruebas** (`test_estrategia` + `test_v9`) y terminar en `OK`. Una de ellas fuerza a propósito un error en la estrategia y por eso imprime un `Traceback`: eso es lo esperado, lo que comprueba es que el bot responde `check` en vez de caerse.

## 2. Estructura del repositorio

- `bot.py` / `conectar.bat`: lanzadores compatibles desde la raíz.
- `app/`: código fuente de producción en Python 3.8+ puro (`estrategia.py`, `preflop.py`, `postflop.py`, `motor.py`, `abstraccion.py`, `blueprint_data.py`, `rangos.py`, `stats.py`, `subjuego.py`, `torneo.py`, `turno.py`, `conexion.py`).
- `tests/`: pruebas unitarias, de protocolo TCP y fuzz (`test_estrategia.py`, `test_v9.py`).
- `docs/`: documentación técnica, auditoría de v8, diseño de v9, reporte experimental y `CHANGELOG.md`.
- `tools/`: simulador PokerFight (`simulador_pf.py`), bots del kit (`bots_kit.py`), benchmark pareado (`benchmark.py`), entrenador C++ (`trainer_cfr.cpp`) y arena HUNL contra Slumbot2019 (`slumbot_arena.py`).
- `backup_v8/`: copia intacta de referencia de Pokalatro v8.

## 3. Conectarse al servidor de pruebas

El token es secreto: se pasa **solo por la línea de comandos**, nunca escrito en un
archivo del repositorio.

```powershell
$env:TOKEN = "pfb-TU-TOKEN-AQUI"
python bot.py -addr 32.201.29.142:9000 -token $env:TOKEN -persistente -verboso
```

En Mac o Linux:

```bash
export TOKEN="pfb-TU-TOKEN-AQUI"
python3 bot.py -addr 32.201.29.142:9000 -token "$TOKEN" -persistente -verboso
```

Qué esperar:

- `Pokalatro conectado como j-xxxxxx (silla N)`: la conexión funciona.
- Después, silencio hasta que arranque la ronda siguiente. **Es normal, no cerrar.**
- Con `-verboso` se ve cada decisión con su motivo y cuántos milisegundos tardó.

Antes de conectarse, revisar en las bases que la dirección siga vigente: el servidor
puede cambiar de IP.

## 3. El día del torneo

```powershell
$env:TOKEN = "pfb-TU-TOKEN-AQUI"
python bot.py -addr HOST:PUERTO -token $env:TOKEN -persistente
```

`-persistente` hace que el bot nunca se rinda: si se corta la red, vuelve a conectarse
con el mismo token y la mesa le devuelve su silla y sus fichas.

Checklist:

- [ ] Una sola copia del bot corriendo. Una segunda conexión con el mismo token
      **reemplaza** a la primera y deja al equipo fuera.
- [ ] Suspensión e hibernación desactivadas, cargador conectado.
- [ ] Red estable (cable si se puede).
- [ ] Conectado antes de la hora anunciada, y dejarlo corriendo todas las rondas.
- [ ] No cerrar la terminal aunque no llegue nada: entre rondas el socket queda en
      silencio a propósito.

Si algo sale mal, sin cerrar nada: el bot reconecta solo. Si hay que reiniciarlo, basta
volver a lanzar el mismo comando; la mesa reconoce el token y devuelve la silla.

## 4. Probar en local contra los bots del kit (opcional)

Con el kit compilado (`go build -o bin/ ./cmd/... ./bots/...`), en una terminal:

```powershell
.\bin\mesa.exe -addr :9000 -jugadores 5 -min-jugadores 5 -rondas 50 -stack 10000 `
  -ciega-chica 50 -ciega-grande 100 -timeout-ms 5000
```

En otras terminales, cuatro rivales y el bot:

```powershell
.\bin\darwin.exe -addr localhost:9000 -token rival-1
.\bin\estadistico.exe -addr localhost:9000 -token rival-2
.\bin\espia.exe -addr localhost:9000 -token rival-3
.\bin\bandido.exe -addr localhost:9000 -token rival-4
python bot.py -addr localhost:9000 -token mi-bot -verboso
```

En la arena local cualquier texto sirve como token: el real solo hace falta contra el
servidor de la organización.

## Problemas frecuentes

| Qué ves | Qué hacer |
| --- | --- |
| `credencial desconocida` | Copiar de nuevo el token completo, sin espacios. |
| La conexión se rechaza | El servidor puede estar apagado o haber cambiado de IP: revisar la tabla de las bases. |
| Conectó pero no llegan mensajes | Está esperando la ronda siguiente. Esperar sin cerrar. |
| Se desconecta sin motivo | Revisar que no haya otra copia corriendo con el mismo token. |
| `python` no se reconoce | Instalar Python desde python.org marcando "Add to PATH", o usar `py` en lugar de `python`. |
