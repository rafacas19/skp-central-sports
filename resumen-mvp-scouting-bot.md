# Resumen MVP — Bot de Scouting para Partidos en Vivo (Telegram)

> **Para:** Aprobación del equipo
> **Qué es este documento:** Resumen de requisitos y flujo de uso del MVP.
> **Qué NO es:** Un diseño técnico ni una definición de tecnología.

---

## El problema

Actualmente nuestros agentes de scouting evalúan partidos en vivo con **papel y lápiz**, anotando acciones positivas y negativas de cada jugador. Este método es lento, propenso a errores, difícil de consolidar y no genera datos reutilizables.

## La oportunidad

Un **bot de Telegram** que permite al agente capturar observaciones de manos libres (por voz o texto) durante el partido. El bot estructura automáticamente esas observaciones por jugador y genera un **informe post-partido** listo para usar, todo dentro de Telegram.

Telegram se elige porque los agentes ya lo tienen en el móvil: sin instalaciones, funciona con una sola mano y es práctico en el estadio (vista en el campo, no en el teclado).

## Alcance del MVP

El MVP se enfoca en **capturar y reportar un solo partido excepcionalmente bien**. Las funciones de base de datos histórica de jugadores (perfiles, búsqueda, tendencias entre partidos) quedan **explícitamente fuera del alcance — Fase 2**.

---

## Decisiones clave acordadas

| Tema | Decisión |
|------|----------|
| **Carga de la alineación** | El agente envía una foto de la alineación → el bot extrae los jugadores → **el agente confirma o corrige** antes de empezar a tomar notas |
| **Identificación del jugador** | Se resuelve por **número de camiseta + posición + nombre**; el bot solo pregunta cuando hay ambigüedad |
| **Profundidad de la nota** | **Sentimiento (positivo/negativo) + categoría de habilidad + cita textual** por cada observación |
| **Entrega del informe** | Dentro de **Telegram**: un resumen formateado **+ un archivo descargable**. Sin app aparte. |
| **Alcance de datos** | **Solo por partido** en el MVP. Perfiles entre partidos = Fase 2. |
| **Manejo de errores en vivo** | Las notas claras se registran en silencio; el bot **solo pregunta si hay duda**; el agente puede corregir en cualquier momento |
| **Equipos** | Se capturan **ambos equipos**; el agente puede marcar opcionalmente un jugador o equipo objetivo |
| **Continuidad de la sesión** | **Resiliente**: persiste ante desconexiones, se retoma simplemente enviando más notas; **una sesión activa por agente** |
| **Notas de equipo** | Foco en jugadores, con una sección ligera de **notas de equipo en texto libre** en el informe |

---

## Flujo de uso (camino principal)

1. **Iniciar sesión** — El agente abre una nueva sesión de partido; el bot pide la alineación.

2. **Configurar la alineación** (punto crítico de precisión) — El agente envía la foto de la alineación. El bot extrae los jugadores de **ambos equipos** (nombre, número, posición) y los muestra para que el agente **confirme o corrija**. Una vez confirmados, la sesión queda lista para capturar.

3. **Capturar observaciones** (el bucle central) — El agente envía mensajes de **voz o texto** en lenguaje natural (ej. *"el número 8, gran control, dejó atrás a dos rivales"* o *"el lateral izquierdo otra vez lento al girar"*). El bot transcribe, identifica al jugador, clasifica el sentimiento y la habilidad. Si tiene certeza, lo registra en silencio; si hay duda, hace **una pregunta rápida**. El agente puede corregir en cualquier momento.

4. **Finalizar sesión** — El agente cierra el partido y el bot genera el informe.

5. **Entrega del informe** — El bot publica un resumen en el chat y adjunta un archivo descargable. El informe incluye: encabezado del partido, resumen por jugador (positivos vs. negativos, habilidades observadas, citas destacadas), jugadores objetivo destacados y la sección de notas de equipo.

---

## Situaciones especiales contempladas

- **Reanudar tras una desconexión** — Si se cae la señal o se apaga el móvil, la sesión sigue activa; el agente continúa enviando notas sin volver a configurar nada.
- **Aviso por inactividad** — Si una sesión queda abierta demasiado tiempo, el bot pregunta *"¿Sigues observando este partido?"* para evitar sesiones olvidadas.
- **Correcciones** — Deshacer la última nota, reasignar jugador, cambiar el sentimiento o la categoría, en cualquier momento.
- **Jugador no listado** — Si se menciona un jugador que no está en la alineación (suplente o no detectado), el bot ofrece agregarlo al momento.

---

## Fuera del alcance del MVP

- ❌ Perfiles de jugadores entre partidos, búsqueda y análisis de tendencias (**Fase 2 — la "base de datos de reclutamiento"**)
- ❌ Panel web o aplicación aparte
- ❌ Integraciones con CRM externo o fuentes de datos de plantillas
- ❌ Múltiples sesiones simultáneas por agente
- ❌ Calificaciones numéricas, marcado por minuto o modelado detallado de eventos
- ❌ Análisis táctico estructurado de formaciones

---

## Preguntas abiertas para definir con el negocio

1. **Categorías de habilidad** — Confirmar el listado inicial de habilidades con los scouts reales. Es la palanca más importante para la utilidad del informe.
2. **Formato del informe** — ¿PDF, texto, o ambos? ¿Qué secciones usa realmente un reclutador para decidir?
3. **Taxonomía de posiciones** — ¿Genérica (DEF/MED/DEL) o específica (LI, MCD, EX)?
4. **Idiomas** — ¿En qué idiomas hablarán los scouts en sus notas de voz?
5. **Formatos reales de alineación** — Recopilar 5 a 10 ejemplos reales de cómo reciben las alineaciones los scouts, para validar el flujo de confirmación antes de construir.

---

## Criterios de éxito del MVP

- Un scout puede cubrir un partido completo **sin tocar papel y lápiz**.
- La confirmación de la alineación toma **menos de 1 minuto**.
- El bucle de captura es **rápido y no invasivo** — las notas claras nunca interrumpen.
- El informe post-partido es **utilizable de inmediato**, sin limpieza manual.
- La tasa de error en la asignación de jugadores es **lo bastante baja para confiar en el informe**.

---

## Visión Fase 2 (no se construye ahora)

Una vez probada la captura por partido, el siguiente paso natural es la **base de datos de reclutamiento entre partidos**: identificar al mismo jugador en distintos partidos y equipos, acumular perfiles y permitir búsqueda y filtrado por habilidad, posición y tendencias. Esta es la parte de la visión original que "optimiza el proceso de reclutamiento". Al mantener los datos estructurados desde el MVP (sentimiento + habilidad + cita), ya generamos la materia prima para esta fase.
