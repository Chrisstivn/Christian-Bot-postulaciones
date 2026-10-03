FROM python:3.10-slim
RUN apt-get update && apt-get install -y xvfb x11vnc fluxbox && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY . .
# Instala tus librerías de python aquí
RUN pip install -r requirements.txt
CMD ["/bin/bash", "-c", "Xvfb :99 -screen 0 1280x720x24 & export DISPLAY=:99 && fluxbox & x11vnc -display :99 -forever -nopw -rfbport 5900 -noxdamage & python3 main.py || tail -f /dev/null"]
