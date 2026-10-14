# Entrenar a Agent IA USB (Fase 2: fine-tuning)

Esta carpeta sirve para **entrenar el modelo de Agent IA USB con conversaciones propias de Agent IA USB**. Así se vuelve más constante en:

- usar sus 33 herramientas correctamente;
- responder siempre en español;
- el estilo propio de Agent IA USB.

Tu PC no tiene GPU, así que el entrenamiento se hace **gratis en la nube**, con Google Colab o Kaggle, desde tu propia cuenta.

> **Qué mejora y qué no:** el fine-tuning hace que Agent IA USB sea más precisa con *sus* herramientas y con su forma de responder. No la hace "más inteligente" en general. Por eso siempre se compara contra el modelo original y **solo se adopta si gana o empata**.

## Qué hay aquí

| Archivo | Para qué |
|---|---|
| `make_dataset.py` | Genera los datos de entrenamiento: ~2.500 conversaciones en `data/`. Mezcla ejemplos sintéticos realistas con tus conversaciones reales exitosas (`data/trajectories.jsonl`). |
| `agent_ia_usb_finetune.ipynb` | El cuaderno que se ejecuta en Colab o Kaggle. |
| `install_model.py` | Instala el modelo entrenado, lo prueba contra el original y lo adopta solo si es mejor o igual. |
| `data/trajectories.jsonl` | Se llena solo: cada tarea que Agent IA USB completa bien (en el uso diario o en `eval_agent.py`) queda guardada como ejemplo. |
| `eval_results.jsonl` | Historial de puntajes de las evaluaciones. |

## Paso a paso

### 1. Generar los datos (en tu PC)

Doble clic no sirve aquí. Abre una terminal en la carpeta de Agent IA USB y ejecuta:

```
02_RUNTIME\python\python.exe 08_TRAINING\make_dataset.py
02_RUNTIME\python\python.exe 08_TRAINING\make_dataset.py --validate
```

Debe decir `2500 OK, 0 with problems`. Se crean `train.jsonl`, `val.jsonl` y `tools.json` en `08_TRAINING\data`.

Mientras más uses Agent IA USB antes de entrenar, más ejemplos reales tendrá.

### 2. Entrenar en Google Colab (gratis)

1. Entra a https://colab.research.google.com con tu cuenta de Google.
2. **Archivo → Subir cuaderno** → elige `08_TRAINING\agent_ia_usb_finetune.ipynb`.
3. **Entorno de ejecución → Cambiar tipo de entorno de ejecución → T4 GPU → Guardar**.
4. **Entorno de ejecución → Ejecutar todo**.
5. En la tercera celda aparece **Elegir archivos**. Selecciona `train.jsonl`, `val.jsonl` y `tools.json` de `08_TRAINING\data`.
6. Espera unas 2–3 horas. No cierres la pestaña; Colab gratis se desconecta si queda inactivo mucho tiempo.
7. Al final te pide permiso para Google Drive y guarda `Qwen3.5-4B-AGENT-IA-USB-Q4_K_M.gguf` (~2,7 GB) en `Mi unidad/agent-ia-usb/`.

**Alternativa Kaggle** (30 h de GPU gratis por semana):

1. Ve a *Datasets → New Dataset* y sube la carpeta `data`.
2. Crea un *Notebook*, importa `agent_ia_usb_finetune.ipynb` y añade el dataset con *Add Input*.
3. En *Settings → Accelerator* elige **GPU T4 x2**.
4. Pulsa *Run All*. El `.gguf` queda en *Output*.

> **¿Y el 9B?** Entrenar Qwen3.5-9B necesita una GPU de **24 GB o más**: Colab Pro con L4/A100, o una GPU alquilada en RunPod o Vast.ai, por unos US$0,40–0,80 la hora. En la primera celda del cuaderno cambia `MODEL = "unsloth/Qwen3.5-9B"` y `OUTPUT_NAME = "Qwen3.5-9B-AGENT-IA-USB"`. La GPU gratis T4 solo alcanza para el **4B**.

### 3. Instalar y comparar (en tu PC)

1. Descarga el `.gguf` desde Drive o Kaggle y cópialo a `08_TRAINING\` en la USB.
2. Ejecuta:

```
02_RUNTIME\python\python.exe 08_TRAINING\install_model.py 08_TRAINING\Qwen3.5-4B-AGENT-IA-USB-Q4_K_M.gguf
```

Este comando:

1. Mueve el archivo a `01_MODEL`.
2. Lo registra en `06_CONFIG\models.json`.
3. Evalúa **el original y el entrenado con las mismas tareas reales**.
4. Muestra la comparación.

Solo si el entrenado **iguala o supera** al original queda como modelo por defecto. Siempre puedes cambiar a mano dentro de Agent IA USB con el comando `model fast-agent-ia-usb` o `model fast`.

### 4. Mejora continua

Cada cierto tiempo:

1. Vuelve a generar los datos (paso 1). Ahora incluirán las nuevas conversaciones reales exitosas.
2. Repite los pasos 2 y 3.

Agent IA USB aprende de sus propios aciertos.

## Notas técnicas

- **Método:** LoRA en 16 bits (r=16) sobre las capas de texto con Unsloth. Unsloth no recomienda QLoRA 4-bit para Qwen3.5.
- **Qué se entrena:** solo los turnos de Agent IA USB (`train_on_responses_only`). Los mensajes del usuario y los resultados de herramientas no se aprenden.
- **Visión:** el proyector de visión (`mmproj`) no se toca, así que el modelo entrenado sigue viendo imágenes con el mismo archivo.
- **Formato de los datos:** el mismo prompt de sistema y los mismos esquemas de herramientas que Agent IA USB usa al ejecutarse, generados desde `03_AGENT\core\prompts.py` y `core\tools.py`. Si cambias herramientas o el prompt, vuelve a generar los datos.
