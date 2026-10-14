# Agent IA USB — Informe baseline (50 conversaciones × 2 modelos)

**Fecha:** 2026-10-06 · **PC de prueba:** DESKTOP-MAD8GHQ (AMD Ryzen 5 5600G, 16 GB RAM, sin GPU dedicada), CPU · **Herramienta:** `evaluation/benchmark.py`, muestra estratificada de 50 (10 por dimensión), grading 100% programático (no juez LLM).

> Condición importante: Gemma-4-12B se midió a **contexto 16384** (reducido desde 32768) porque a 32768 este PC de 16 GB quedaba con 0.4 GB de RAM libre y haría swap. Qwen se midió a su config real (32768 + MTP). En un PC con 32 GB+ Gemma correría a 32768 (algo más lento aún).

## Resultados

| Modelo | Score global | conversación | razonamiento | agente (herramientas) | código | memoria | pasos/conv | s/conv |
|---|---|---|---|---|---|---|---|---|
| **Qwen3.5-9B + MTP** | **9.60** | 10.0 | 9.0 | 9.0 | 10.0 | 10.0 | 0.46 | **19.1 s** |
| **Gemma-4-12B** (ctx 16384) | **9.80** | 10.0 | 10.0 | 9.0 | 10.0 | 10.0 | 0.42 | **37.0 s** |

Tiempo total del run: Qwen 956 s (~16 min), Gemma 1851 s (~31 min).

### Latencia por dimensión (s/conversación)
| | conversación | razonamiento | agente | código | memoria |
|---|---|---|---|---|---|
| Qwen3.5-9B+MTP | 11.9 | 6.7 | 34.3 | 34.6 | 8.1 |
| Gemma-4-12B | 15.0 | 9.3 | **89.6** | 57.6 | 13.5 |

## Lectura

1. **Calidad:** ambos son muy buenos. Gemma gana por poco (9.80 vs 9.60), solo en **razonamiento** (10 vs 9). En uso de herramientas (**agente**) los dos se quedan en 9.0 → es la debilidad común y el mejor punto para mejorar.
2. **Eficiencia:** **Qwen es ~2× más rápido** por conversación (19 vs 37 s) gracias a MTP y a ser más pequeño; Gemma es especialmente lento en tareas con herramientas (89.6 s/conv) y aun así corrió con contexto recortado. En CPU y RAM limitada, **Qwen+MTP es la opción eficiente**; Gemma solo donde sobre RAM y no importe esperar.
3. **Pasos:** casi idénticos (~0.4–0.5 pasos/conv). El trabajo de herramientas se resuelve en 1 paso en agente/código; conversación/razonamiento/memoria no usan herramientas (0 pasos), como se espera.
4. **Techo del benchmark:** con puntajes 9–10 el test está casi saturado y no discrimina fino. Para "mejorar el razonamiento" de verdad habría que **subir la dificultad o N** (p. ej. `--sample 200` o `--all-scenarios`), porque a este nivel casi todo da 10.

## Ajustes recomendados (NO aplicados — pediste solo informe)

Palancas y valores concretos, ordenados por impacto:

1. **`max_reply_tokens` 6144 → 1536–2048** en [agent.json](../06_CONFIG/agent.json). Hoy el modelo *puede* generar hasta 6144 tokens por respuesta; es el mayor riesgo de latencia en CPU. Las respuestas reales son cortas, así que bajarlo acelera el peor caso sin perder calidad (y refuerza la brevedad que el grading premia).
2. **`max_steps` 30 → 12–15** en [agent.json](../06_CONFIG/agent.json). Ninguna conversación pasó de ~1–2 pasos; 30 solo es un tope de seguridad excesivo. Bajarlo acota los cuelgues largos si el modelo se enreda.
3. **Contexto de Gemma por RAM** en [models.json](../06_CONFIG/models.json): dejar `"context": 16384` para PCs de ~16 GB (evita swap) y 32768 solo con 32 GB+. Su `min_ram_gb: 32` ya impide que el modo *auto* lo elija en equipos chicos; con `/usar gemma` sigue disponible.
4. **Sampling de Gemma** en [models.json](../06_CONFIG/models.json:9): hoy `temperature 1.0`, que es alto para tareas de herramientas/razonamiento (más variabilidad). Probar **0.7** (top_p 0.9, top_k 40) para respuestas más deterministas en agente/código. Qwen (0.6) está bien.
5. **`enable_thinking`** está **desactivado** (hardcodeado en [llm_client.py:111](../03_AGENT/core/llm_client.py:111)). Si se quiere exprimir razonamiento, se puede A/B activarlo en Qwen — pero cuesta bastante más tiempo en CPU; solo si el objetivo es calidad sobre velocidad.
6. **Mejor test para discriminar:** correr `--sample 200` (o `--all-scenarios`) cuando haya tiempo/RAM, para que las diferencias de razonamiento se noten (el de 50 ya saturó en 9–10).

## Recomendación en una frase
Para el día a día en portátiles modestos, **Qwen3.5-9B + MTP** es la mejor relación calidad/velocidad. **Gemma-4-12B** rinde un punto más en razonamiento pero cuesta el doble de tiempo y necesita más RAM; úsalo en equipos con 32 GB+. Los ajustes 1 y 2 (max_reply_tokens y max_steps) son los que más aceleran sin tocar la calidad, y se pueden aplicar cuando quieras.

---
*Datos crudos: `evaluation/results/*.json` · tabla: `evaluation/reports/report_20261006-004224.md`*
