FROM python:3.11-slim

WORKDIR /app

# Evitar prompts de pip y buffering de Python
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Instalar dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar código
COPY . .

# Crear directorio de datos persistentes
RUN mkdir -p /app/data/sessions

# Puerto para webhook server (Laura usa 8086, Sara usa 8085)
EXPOSE 8086

# Dar permisos al script de arranque
RUN chmod +x start.sh

# Arranca Telegram + Webhook juntos
CMD ["bash", "start.sh"]

