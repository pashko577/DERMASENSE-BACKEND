# RDKit trae un wheel binario de ~150 MB: la imagen se construye sobre una base
# slim y se instala en dos capas para que las dependencias se cacheen aparte del
# codigo. Ver README §7 (Vercel/Lambda quedan descartados por este tamano).
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv

# libxrender y libxext son dependencias nativas de RDKit.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libxrender1 libxext6 \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install .

COPY app ./app

# Render y Fly.io inyectan el puerto por entorno.
ENV PORT=8000
EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
