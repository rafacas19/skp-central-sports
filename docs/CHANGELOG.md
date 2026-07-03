# Novedades del Bot de Scouting

Registro de cambios pensado para el equipo: qué es nuevo y cómo usarlo. Sin
tecnicismos.

---

## Versión de junio de 2026 — «Asistente inteligente»

El bot ahora se comporta menos como una base de datos con comandos y más como un
asistente de scouting. Tú escribes en lenguaje natural (texto o voz) y el bot
**agrupa, cronometra, calcula y exporta** por ti.

### ⏱️ Cronómetro del partido *(nuevo)*
- Controlas el reloj con dos comandos: **`/primer_tiempo`** (arranca en el minuto 0)
  y **`/segundo_tiempo`** (reanuda en el minuto 45).
- `/nuevo` ya **no** arranca el reloj: tú decides el pitido inicial.
- El bot avisa al llegar al **minuto 45 y al 90**; el reloj sigue contando el tiempo
  añadido, no se corta.

### 🕐 Minuto en cada observación *(nuevo)*
- Cada nota queda marcada automáticamente con el **minuto de partido**.
- Si el reloj se desfasa, escribe el minuto real dentro de la observación
  (p. ej. `Ferrin gol min 37`) y el bot **re-sincroniza** el cronómetro.

### 🔄 Sustituciones *(nuevo)*
- Escribe el cambio con normalidad: `Entra Ferrin y sale el número 7`.
- El bot identifica a **quién entra** y lo registra como jugador, para que puedas
  seguir observándolo el resto del partido.
- Si el que entra es un número y no está claro el equipo, el bot pregunta.

### ⭐ Valoración 1–5 y decisión automática *(cambiado)*
- La escala de valoración pasa de 1–10 a **1–5**.
- La **decisión se calcula sola** a partir de la última valoración:

  | Valoración | Decisión        |
  | ---------- | --------------- |
  | 1          | A descartar     |
  | 2          | A seguir        |
  | 3          | Interesante     |
  | 4          | Muy interesante |
  | 5          | A firmar        |

- Ya no necesitas un comando aparte para la decisión en el flujo normal.

### 📊 Informe en Excel *(cambiado)*
- `/fin` ahora genera un **archivo Excel (.xlsx) editable** en vez de un CSV.
- Tres hojas: **Local**, **Visitante** y **Notas equipo**.
- **Una fila por jugador**: todas sus observaciones se agrupan (con sus minutos),
  más su valoración final y decisión.

### 📚 Histórico acumulado *(nuevo)*
- Nuevo comando **`/historico`**: exporta un Excel con **todos los jugadores de
  todos los partidos** (club, fechas, observaciones previas y actuales, valoración,
  decisión y un resumen global). Es tu base de scouting completa.

### 🧹 Flujo más simple *(cambiado)*
- El bot **agrupa** los jugadores repetidos, **actualiza** sus datos desde el
  lenguaje natural y **genera** la decisión automáticamente.
- Los comandos que confundían (`/unir`, `/editar`, `/decision`) ya **no aparecen**
  en la ayuda; el bot los resuelve solo. Siguen disponibles por si acaso.
- El mensaje de bienvenida (`/start`) se reescribió con el flujo actualizado.

---

## Antes (pivote «observación primero»)

El bot ya permitía empezar a observar de inmediato sin subir la alineación:
observaciones por texto y voz, identidad del jugador entre partidos, valoración
manual (1–10), notas de equipo, informe en CSV, informe por jugador con resumen de
IA y detección de nombres duplicados al finalizar. Esta versión construye sobre eso.
