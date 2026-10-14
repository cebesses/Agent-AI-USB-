# Cómo publicar Agent IA USB en GitHub (guía rápida)

Esta guía lleva Agent IA USB desde esta carpeta hasta un repo público con releases
descargables y un post de lanzamiento listo para r/LocalLLaMA.

## 1. Preparar el repo local

```bat
cd C:\Users\g\Downloads\agent-ia-usb
git init
git add .
git commit -m "Agent IA USB v1.0.0: agente IA portable en USB con voz offline"
```

El `.gitignore` ya excluye modelos (`01_MODEL/`), runtimes (`02_RUNTIME/`),
sesiones, backups y datos generados — el commit queda en pocos MB.

## 2. Crear el repo público en GitHub

1. En github.com: **New repository** → nombre `Agent IA USB`, público, sin README
   (ya existe), licencia MIT (ya existe).
2. Subir:

```bat
git remote add origin https://github.com/TU_USUARIO/agent-ia-usb.git
git branch -M main
git push -u origin main
```

## 3. Crear el release v1.0.0

```bat
MAKE-RELEASE.bat v1.0.0
```

Esto genera `agent-ia-usb-v1.0.0.zip` (solo código, ~2 MB). Súbelo:

1. GitHub → Releases → **Draft a new release** → tag `v1.0.0`.
2. Título: `Agent IA USB v1.0.0 — tu J.A.R.V.I.S. en una USB, 100% offline`
3. Descripción: copia los highlights del README + requisitos + quick start.
4. Arrastra `agent-ia-usb-v1.0.0.zip` como asset → **Publish**.

Quien descarga el zip lo descomprime en una USB y ejecuta `SETUP-USB.bat`;
los modelos se bajan solos.

## 4. El GIF de demo (imprescindible)

Nadie descarga lo que no puede ver. Graba 20-30 s con
[ScreenToGif](https://www.screentogif.com/) (gratis, portable):

1. `START-AGENT-IA-USB.bat`, escribe `/voz on`.
2. Di en voz alta: *"Agent IA USB, mueve los PDF de Descargas a Documentos\PDF"*.
3. Muestra la respuesta hablada y la ejecución de la herramienta.

Guarda el GIF en `docs/demo.gif` y ponlo arriba del README.

## 5. Lanzamiento (orden recomendado)

La secuencia que funciona para herramientas de IA locales
(Product Hunt primero, Show HN 2-4 días después con otro ángulo):

1. **r/LocalLLaMA** (839k miembros) — post tipo "Show": título honesto y
   números reales. Plantilla:

   > **Agent IA USB: an AI agent that runs 100% offline from a USB stick
   > (llama.cpp + whisper.cpp + Piper, 36 tools, pure-stdlib Python)**
   >
   > - CPU-only, no GPU needed. 9B Q4 model, ~6 GB RAM.
   > - Voice in/out offline, Spanish TTS included.
   > - Acts on the PC: files, code, security audit, RAG over your docs.
   > - Everything reversible (undo journal), risky actions ask first.
   > - Benchmark: X/Y conversations passed (evaluation/).
   >
   > Repo + download in comments. What would you add?

   La comunidad castiga el fluff y valora benchmarks: usa los números de
   `evaluation/INFORME-baseline-50.md`.

2. **Product Hunt** — martes a jueves, con el GIF como galería y el zip como
   enlace de descarga. Responde todos los comentarios el primer día.

3. **Show HN** (2-4 días después) — ángulo distinto: "Show HN: Agent IA USB, un
   agente que actúa en tu PC sin instalar nada, desde una USB".

4. **r/selfhosted, r/opensource, r/SideProject** — mismo post adaptado.

5. **dev.to / Medium** — artículo "How I built..." a la semana, para cola larga.

## 6. Mantenimiento tras el lanzamiento

- Etiqueta `good first issue` para contribuidores nuevos.
- Responde issues en <24 h las dos primeras semanas.
- Nueva versión = nuevo release + post de actualización en r/LocalLLaMA.
