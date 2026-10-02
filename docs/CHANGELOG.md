# Novedades del Bot de Scouting

Registro de cambios pensado para el equipo: qué es nuevo y cómo usarlo. Sin
tecnicismos.

---

## Versión de octubre de 2026 — «Partido en el panel»

Nuevo en el **panel web**, pensado para usarlo **desde el móvil**. El bot de
Telegram sigue igual, y un partido creado en el panel no se mezcla nunca con el
que tengas abierto en el bot.

### ⚽ Crear un partido desde el panel
En **Partidos**, botón **+ Nuevo partido**. Son tres pasos cortos:

1. **Equipos**: local, visitante, fecha y, si quieres, competición y sede. La
   categoría se separa sola del nombre (`Santa Fe U17` → Santa Fe · Sub-17).
2. **Sistema**: elige cómo forma cada equipo (4-3-3, 4-4-2, 4-2-3-1 o 3-5-2).
3. **Dorsales y nombres**: todo opcional. Rellena solo los que te interesen o
   pulsa **Saltar, ver el campo**.

### 🟩 El campo
Los dos equipos en su sistema, cada puesto con su camiseta, aunque esté vacío
(un puesto vacío muestra solo su abreviatura: *POR*, *DFC*, *LI*…). **Toca un
jugador para puntuarlo**. Si el puesto está vacío, se crea el jugador en ese
momento y le pones nombre o dorsal cuando quieras, también con el partido ya
terminado.

- Los jugadores puntuados llevan un aro amarillo con su nota.
- Con el móvil en horizontal, el campo se gira para aprovechar la pantalla.
- En el menú **⋮** del campo: *Dorsales y nombres*, *Cambiar sistema* y
  *Finalizar partido*.

### 📝 Puntuar por perfil
La ficha de cada jugador usa **vuestro perfil por posición** (Arquero, Defensa
Central, Lateral, Medio Centro, Interior, Extremo, Centro Delantero), con los mismos
criterios y descripciones de vuestra hoja *Perfiles Scout*.

- Una sección cada vez: **Técnica · Defensa · Ataque · Condición · Mental ·
  Físico**, con el avance de cada una (por ejemplo *3/5*).
- Cada criterio, del **1 al 5**. Toca de nuevo la nota para quitarla. Toca el
  nombre del criterio para leer su descripción.
- **Se guarda solo** con cada toque.
- La **valoración del partido es la media** de lo puntuado, y de ahí sale la
  decisión como siempre (por ejemplo, 3,46 → *Interesante*). Si puntúas un
  partido antiguo, no pisa la valoración de uno más reciente.
- Los **porteros** se puntúan con vuestra hoja **Arquero** (Técnica, Táctica
  defensiva, Táctica ofensiva, Condicional y Mental, 27 criterios). Quien prefiera
  puede seguir dándoles una valoración única de 1 a 5 (*Perfil → Sin perfil*).

### 📄 Informe de perfil (PDF)
En la ficha del jugador, **Informe de perfil**: la misma hoja que vuestro
ejemplo, con una columna por partido puntuado, la media de cada sección
(*1. Técnica — 3,5/5*) y las observaciones.

- **Generar texto con IA** redacta el resumen y un párrafo por sección a partir
  de las notas; luego lo puedes editar.
- Si editas el texto, **nunca se sobrescribe solo**: si llegan evaluaciones
  nuevas, el informe te avisa y tú decides si lo regeneras.
- **Descargar PDF**: una página, lista para compartir.

### 📷 Foto y vídeo
En **Editar** jugador puedes subir una **foto** (JPG, PNG o WEBP, hasta 5 MB),
que sustituye a la de Telegram, y añadir un **enlace de vídeo**, que aparece en
el informe.

### 🔁 Cambios durante el partido
En el campo, **Cambio · ⟨equipo⟩**: eliges quién sale y quién entra (un suplente
o alguien que escribes en ese momento) y, si quieres, el minuto. El que entra
ocupa su puesto con una flecha verde ↑; el que sale pasa al banquillo con ↓ y el
minuto. Si te equivocas, **Deshacer cambio**.

### 📊 Excel de las evaluaciones
En el partido (o en el menú **⋮** del campo), **Descargar Excel**: vuestra hoja
*Perfiles Scout* rellena, una pestaña por perfil, los criterios en filas y **una
columna por jugador**, con su valoración del partido al final.

### ✏️ Editar o borrar un partido
En el partido, **Editar**: fecha, competición, categoría y sede (y los equipos,
si el partido se creó en el panel). Un partido creado en el panel también se puede
**borrar**; los jugadores con nombre siguen en *Jugadores* y su valoración vuelve
a la de su último partido.

### 📶 Sin cobertura en el campo
Si pierdes la señal mientras puntúas, lo que toques se queda guardado en el móvil
y se envía solo cuando vuelve la conexión, aunque cierres la página.

### 🤖 El resumen del jugador lee las puntuaciones
El resumen con IA de la ficha del jugador (y el informe del bot) usa ahora las
puntuaciones por perfil, no solo las notas.

### 📱 Todo el panel en el móvil
- El menú de arriba se recoge en un botón **Menú**.
- Las listas de jugadores, partidos y selecciones se ven como tarjetas.
- Los filtros se pliegan en **Filtros**.
- Todos los botones tienen el tamaño de un dedo.

---

## Versión de septiembre de 2026 — «Selecciones»

Nuevo en el **panel web**. El bot de Telegram no cambia en nada.

### 🇨🇴 Selecciones (convocatorias por categoría)
Hay una pestaña nueva arriba: **Selecciones**. Cada convocatoria es una lista de
jugadores con su categoría.

1. Entra en **Selecciones**.
2. Escribe el nombre y pulsa **Crear**. Por ejemplo: `Selección Colombia U15`.
3. Se guarda como *Colombia* con categoría *Sub-15*.

La categoría se deduce sola del nombre (`U15`, `sub 15`, `Sub-15` son lo mismo;
también entiende *Femenino*, *Juvenil*, *Reserva* y *Profesional*). Si quieres
otra, escríbela a mano en el campo **Categoría**.

Puedes tener tantas como necesites: Colombia Sub-15, Sub-17, Sub-20…

### 👤 Convocar jugadores
Dos caminos:

- **Desde la selección:** abre la lista, elige al jugador en *Convocar jugador* y
  pulsa **Añadir**.
- **Desde la ficha del jugador:** en su perfil, sección *Selecciones*.

Para sacarlo, el botón **Quitar** de su fila.

Ojo, esto es lo importante: **convocar a un jugador no le cambia el club**. Sigue
siendo del Junior o del Nacional, y además aparece en la selección. Es un solo
jugador con dos sitios, no dos fichas. Un mismo jugador puede estar en varias
selecciones a la vez, y las listas de años anteriores se quedan como estaban.

### 📊 Descargar el Excel
Botón **Descargar Excel**, arriba a la derecha de cada selección. Sale con las
mismas nueve columnas de siempre y en el mismo orden: NOMBRE · APELLIDO · EDAD ·
POSICIÓN · PIERNA HABIL · CLUB · AGENTE · VALORACIÓN · SEGUIMIENTO.

Lo que ves en pantalla es exactamente lo que se descarga. Todo se rellena con lo
que ya tiene la ficha del jugador, así que una casilla vacía se arregla editando
al jugador, no el Excel.

- **EDAD** es el año de nacimiento, como lo llevas tú.
- **AGENTE** pone `Sin Agente` cuando no hay ninguno.
- **SEGUIMIENTO** es la decisión: sale de la valoración, salvo que hayas puesto
  otra a mano.

### 🔧 Y de paso
- **El agente ya estaba**: en la ficha del jugador y en **Editar**, campos *Agente*
  y *Teléfono del agente*.
- **Fusionar no borra convocatorias**: al unir dos fichas del mismo jugador, sus
  selecciones pasan a la ficha que se queda.
- **Jugadores de dos posiciones**: quien juega de *Defensa central, Mediocentro
  defensivo* ya aparece al filtrar por cualquiera de las dos.

---

## Versión de agosto de 2026 — «Panel: jugadores y seguimiento»

Todo esto es del **panel web**, no del bot. El bot funciona igual.

### ➕ Crear jugadores a mano
Antes solo aparecían los jugadores nombrados en un partido. Ahora puedes añadir
uno tú mismo: un recomendado, alguien que viste sin estar observando.

1. Entra en **Jugadores**.
2. Botón **«+ Nuevo jugador»**, arriba a la derecha.
3. El nombre es obligatorio; lo demás (equipo, posición, edad…) puedes dejarlo
   en blanco y completarlo luego.

Si ya existe un jugador con ese nombre y equipo, el panel te avisa y te enlaza a
su ficha en vez de crear un duplicado.

### 🏷️ Categoría del equipo automática
Si escribes **«Santa Fe U18»**, el sistema guarda el club (*Santa Fe*) y la
categoría (*Sub-18*) por separado. Tú sigues escribiendo como siempre.

- Reconoce `Sub-18`, `sub 18`, `U18`, `u-18` y también *Juvenil*, *Reserva*,
  *Femenino* y *Profesional*.
- Si el nombre no lleva categoría, no pasa nada: se queda en blanco.
- Ventaja: «Santa Fe» y «Santa Fe U18» ya son el mismo club, así que un jugador
  no se te parte en dos fichas.

### 📞 Seguimiento de contacto
Cada jugador tiene ahora un estado de contacto, para saber por dónde va la
conversación: **Sin contactar · Contactado · En conversación · Reunión agendada ·
Acuerdo · Descartado**.

- **Cambiarlo rápido:** abre la ficha del jugador, sección *Seguimiento*, y pulsa
  el estado. Se guarda la fecha de hoy como último contacto.
- **Con detalle:** en **Editar** puedes poner la fecha exacta y las notas de la
  conversación (con quién hablaste, próximos pasos).
- **Ver quién falta:** en la lista de **Jugadores** hay una columna *Contacto* y
  un filtro. Filtra por *Sin contactar* y tienes tu lista de llamadas.

Ojo: el estado de contacto es **independiente** de la decisión deportiva. Un
jugador puede ser «A firmar» en el campo y «Descartado» en la negociación.

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
