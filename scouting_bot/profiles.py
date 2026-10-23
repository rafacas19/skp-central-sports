"""Position scouting profiles — the client's "Detección de talento" sheets.

Each profile is the list of criteria a scout scores 1–5 for one kind of player
(Defensa Central, Lateral, …), grouped into the client's five sections, plus the
build and height the position asks for (checked Sí/No, never averaged). The text
is transcribed verbatim from the client's workbooks (`feedback/Perfiles_Scout.xlsx`,
one sheet per outfield profile, and `feedback/Perfiles_Scout_Arquero.xlsx` for the
goalkeeper). Spelling fixes only ("Concetración", "reamtes", "Aereo", "area").
In the goalkeeper sheet, a second "1.5 Despeje" copied from the Defensa Central
sheet is left out, and "5.1 Concentración" takes the concentration text the
client placed under "4.1 Velocidad de Reacción", whose own description is still
to come from them. Updating a profile
means editing this module — there is no admin screen for criteria.

A score sheet's numbers flow into the existing single rating: the match rating
is the mean of every criterion scored (`match_rating`), which is what the
decision is derived from. The report's per-section averages use the client's
presentation instead — one decimal, rounded half-up, comma separator ("3,7").

Stdlib only.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from .positions import canonical_position

SCORE_MIN = 1
SCORE_MAX = 5
SCORES = tuple(range(SCORE_MIN, SCORE_MAX + 1))


@dataclass(frozen=True)
class Criterion:
    code: str  # "1.1" … "3.10", unique within a profile
    name: str
    description: str


@dataclass(frozen=True)
class Section:
    number: int  # 1–5
    header: str  # as the sheet writes it: "2. TÁCTICA DEFENSIVA"
    criteria: tuple[Criterion, ...]

    @property
    def title(self) -> str:
        """Sentence-case title for prose: "Táctica defensiva"."""
        return self.header.split(". ", 1)[-1].capitalize()

    @property
    def short(self) -> str:
        """One word for a tab on a phone: "Defensa" for Táctica defensiva."""
        return SECTION_SHORT.get(self.number, self.title)


# Tab labels for the score sheet — the five sections every profile shares.
SECTION_SHORT = {1: "Técnica", 2: "Defensa", 3: "Ataque", 4: "Condición", 5: "Mental"}


@dataclass(frozen=True)
class Profile:
    key: str
    name: str
    sections: tuple[Section, ...]
    build: str  # contextura the position asks for ("Atlético")
    height: str  # estatura ("Alto", "Indiferente")

    @property
    def criteria(self) -> tuple[Criterion, ...]:
        return tuple(c for s in self.sections for c in s.criteria)

    @property
    def codes(self) -> frozenset[str]:
        return frozenset(c.code for c in self.criteria)


PROFILES: tuple[Profile, ...] = (
    Profile(
        'arquero', 'Arquero',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Control y Recepción',
                      'Controlar y asegurar el balón tras recibirlo, especialmente ante pases de compañeros o situaciones de presión, permitiendo dar continuidad al juego.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón con precisión y en el momento adecuado hacia compañeros cercanos o alejados, facilitando la progresión del equipo.'),
            Criterion('1.3', 'Conducción',
                      'Trasladar el balón pegado al pie y con cabeza levantada, manteniendo la posesión, a fin de poder fijar, superar una línea/avanzar y/o regular el ritmo de la jugada.'),
            Criterion('1.4', 'Juego con los pies',
                      'Utilizar ambos pies para controlar, conducir y jugar el balón bajo diferentes niveles de presión, manteniendo la posesión y dando continuidad al juego.'),
            Criterion('1.5', 'Blocaje',
                      'Asegurar el balón mediante una correcta técnica de manos y cuerpo, evitando segundas acciones del rival.'),
            Criterion('1.6', 'Desvío / Despeje',
                      'Intervenir sobre el balón cuando no es posible asegurar la posesión, desviándolo o despejándolo hacia una zona segura.'),
            Criterion('1.7', 'Juego Aéreo',
                      'Dominar balones elevados mediante blocaje, despeje o salida, controlando correctamente el espacio y la trayectoria del balón.'),
            Criterion('1.8', 'Saque/Distribución',
                      'Iniciar el juego mediante saque con mano o pie, buscando precisión, ventaja y continuidad para el equipo.'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', 'Posicionamiento',
                      'Ocupar correctamente la posición respecto al balón, portería, compañeros y rivales, reduciendo los espacios de finalización.'),
            Criterion('2.2', 'Profundidad Defensiva',
                      'Controlar el espacio detrás de la última línea defensiva, estando preparado para intervenir ante balones a la espalda de los defensores.'),
            Criterion('2.3', 'Juego Aéreo Defensivo',
                      'Interpretar trayectorias y decidir cuándo salir, blocar, despejar o permanecer en portería ante centros y balones elevados.'),
            Criterion('2.4', '1v1 Defensivo',
                      'Resolver situaciones de mano a mano, reduciendo el ángulo de finalización y evitando la progresión o remate del atacante.'),
            Criterion('2.5', 'Control de área',
                      'Dominar el espacio cercano a la portería, interviniendo sobre centros, pases y acciones que ingresen en su zona de influencia.'),
            Criterion('2.6', 'Anticipación',
                      'Leer previamente la trayectoria del balón y las intenciones del rival para intervenir antes de que la acción llegue a una situación de finalización.'),
            Criterion('2.7', 'Defensa de remates',
                      'Resolver remates desde diferentes distancias, ángulos y superficies, utilizando correctamente posición, manos, pies y cuerpo.'),
            Criterion('2.8', 'Segunda Acción',
                      'Reaccionar después de una primera intervención, estando preparado para rechaces, rebotes o nuevas acciones de finalización.'),
            Criterion('2.9', 'Organización Defensiva',
                      'Coordinar y orientar a sus compañeros para mantener el orden defensivo, especialmente ante centros, ataques y situaciones de balón parado.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', 'Vista Previa',
                      'Observar el entorno antes de recibir el balón para identificar compañeros, rivales y espacios disponibles.'),
            Criterion('3.2', 'Apoyo Fuera del Área',
                      'Participar defensivamente fuera del área cuando la situación lo exige, interpretando correctamente profundidad y distancia respecto a la última línea.'),
            Criterion('3.3', 'Toma de Decisión',
                      'Seleccionar correctamente entre pase corto, pase largo, conducción, saque o conservación del balón según la situación del juego.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Velocidad de Reacción',
                      ''),
            Criterion('4.2', 'Agilidad',
                      'Cambiar de dirección, desplazarse, frenar y reaccionar ante diferentes estímulos en el menor tiempo posible.'),
            Criterion('4.3', 'Coordinación',
                      'Coordinar desplazamientos, saltos, manos, pies y cuerpo para ejecutar correctamente las intervenciones.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Concentración',
                      'Mantener la atención durante todo el partido, incluso en períodos con poca participación directa.'),
            Criterion('5.2', 'Confianza/ Valentía',
                      'Tomar decisiones y asumir riesgos necesarios en situaciones de presión, centros, salidas y duelos.'),
            Criterion('5.3', 'Liderazgo',
                      'Influir positivamente en sus compañeros mediante comunicación, orientación y capacidad para organizar la última línea.'),
            Criterion('5.4', 'Gestión Del Error',
                      'Recuperarse rápidamente después de un error, manteniendo la concentración y participación en el juego.'),
        )),
        ),
        build='Atlético',
        height='Alto',
    ),
    Profile(
        'defensa_central', 'Defensa Central',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Control',
                      'Dominar el balón tras recepción, especialmente en espacios cortos y entre líneas, de manera que le genere una ventaja para poder dar así continuidad al juego, mediante: atraer, ir a fijar un rival, pasar, ganar metros, o eliminar la última línea.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón en tiempo y forma a diferentes distancias, de manera que el compañero reciba con ventaja. Especialmente en construcción, a fin de que el receptor pueda dar progresión directa o vía tercer hombre. Ej. diagonal 2, 3 y 4.'),
            Criterion('1.3', 'Conducción',
                      'Trasladar el balón pegado al pie y con cabeza levantada, manteniendo la posesión, a fin de poder fijar, superar una línea/avanzar y/o regular el ritmo de la jugada.'),
            Criterion('1.4', 'Juego Aéreo',
                      'Disputar y ganar duelos aéreos, y dirigir el balón a un objetivo deseado.'),
            Criterion('1.5', 'Despeje',
                      'Alejar el peligro y ofrecer la posibilidad de generar continuidad ofensiva al equipo a través de una acción ofensiva posterior.'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', 'Posicionamiento Defensivo',
                      'Identificar los espacios que se deben ocupar en función del posicionamiento de sus compañeros y del peligro que suponen los jugadores rivales cuando se enfrenta a la última línea, de manera que pueda estar en disposición para realizar las funciones previstas en el trabajo de línea y así minimizar riesgos.'),
            Criterion('2.2', 'Perfil Defensivo',
                      'Orientar el cuerpo de manera que, cuando está en el lado activo, pueda inducir al rival al comportamiento deseado a fin de que no progrese el ataque, o que cuando está en el lado débil/contrario, pueda dominar referencias y anticipar la acción.'),
            Criterion('2.3', 'Marcaje Individual',
                      'Mantener proximidad respecto al jugador rival que le corresponde tomar en su zona, para evitar que participe del juego o que reciba el balón con comodidad. Además debe evitar perder las demás referencias del juego y/o dejarse llevar solo por el balón. Dentro de área, en fase de finalización, identificar la marca significa que el rival esté a una distancia máxima de un brazo. En este caso se tiene que mantener la marca individual hasta el final de la acción. Fuera del área, en fase de progresión, si el defensor está en el lado activo o fuerte, identificar la marca significa que el atacante rival esté a una distancia máxima de dos brazos. En este caso se tiene que mantener la marca mientras el rival esté en la zona del defensor.'),
            Criterion('2.4', '1v1 Defensivo',
                      'Evitar que el rival le supere y progrese, ya sea a través de un robo de balón, de manera que recupera la posesión, interrumpiendo la acción u obligando a retardar el ataque rival.'),
            Criterion('2.5', 'Anticipo',
                      'Dominar las distancias respecto al rival, perfil y lectura de trayectorias, a fin de que cuando en ventaja, se pueda evitar que el receptor rival contacte el balón, ya sea interrumpiendo la acción rival o recuperando la posesión del balón.'),
            Criterion('2.6', '2do Balón',
                      'Ganar segundas opciones a través de un buen posicionamiento producto de la lectura de trayectoria del balón y de agresividad en la participación, cuando por función propia de la posición, le corresponde estar cercano a balón.'),
            Criterion('2.7', 'Defender Espacios Amplios',
                      'Eliminar o reducir el peligro de la acción rival, gracias su posicionamiento, anticipo, perfil y 1v1 defensivo, cuando se encuentra lejos del arco propio y con escasas posibilidades de recibir ayudas de compañeros.'),
            Criterion('2.8', 'Defender Hacia Delante',
                      'Asumir iniciativa cuando en ventaja, dando pasos hacia delante para inducir al poseedor del balón al comportamiento deseado, limitar sus opciones y mantener el bloque corto.'),
            Criterion('2.9', 'Ritmo Defensivo',
                      'Repetir acciones defensivas de manera continua, sin pararse: posicionamiento, marcaje, anticipo, 2do balón y defensa hacia delante.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', 'Búsqueda de Progresión',
                      'Superar líneas rivales, ya sea mediante un pase o una conducción al espacio, que facilite la continuidad vertical en la acción posterior del equipo.'),
            Criterion('3.2', 'Vigilancia y Acompañamiento',
                      'Ocupar un espacio que le permite mantener el bloque corto, en disposición de participar y de controlar a posibles receptores rivales cuando el equipo progresa. Suele ser en campo rival.'),
            Criterion('3.3', 'Incorporación Ofensiva',
                      'Facilitar progresión ofreciendo una línea de pase por detrás de la presión rival.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Velocidad',
                      'Cubrir una distancia dada, en el menor tiempo posible.'),
            Criterion('4.2', 'Agilidad',
                      'Cambiar de dirección, arrancar, parar y responder a estímulos, en el menor tiempo posible.'),
            Criterion('4.3', 'Potencia',
                      'Imponer presencia física en el cuerpo a cuerpo para mantener o recuperar la posición en una disputa, producto de la fuerza, velocidad y uso adecuado del cuerpo.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Combativo/Competitivo',
                      'Desear de forma continua imponerse y superar al rival, especialmente ante condiciones adversas, y siempre dentro del marco de las reglas del juego.'),
            Criterion('5.2', 'Confianza/ Valentía',
                      'Asumir y mantener predisposición para asumir riesgos medidos a pesar de las dificultades.'),
            Criterion('5.3', 'Liderazgo',
                      'Contribuir a que el equipo alcance su objetivo, a través de su capacidad para influir en compañeros, de una comunicación asertiva y deseo del balón'),
            Criterion('5.4', 'Auto Conocimiento',
                      'Controlar las emociones que pueden afectar el rendimiento, a fin de gozar de auto control en momentos de estrés, adversidad, cansancio, nerviosismo.'),
        )),
        ),
        build='Atlético',
        height='Alto',
    ),
    Profile(
        'lateral', 'Lateral',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Control',
                      'Dominar el balón tras recepción, especialmente en espacios cortos y entre líneas, de manera que le genere una ventaja para poder dar así continuidad al juego, mediante: atraer, ir a fijar un rival, pasar, ganar metros, o superar una línea. En Construcción, especial atención para recepción por dentro. En Finalización, especial importancia a la orientación del control para sacar ventaja y centrar o rematar.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón en tiempo y forma a diferentes distancias, de manera que el compañero reciba con ventaja. Especialmente en acciones de finalización a través de centros.'),
            Criterion('1.3', 'Conducción',
                      'Trasladar el balón pegado al pie y con cabeza levantada, manteniendo la posesión, a fin de poder fijar, superar una línea/avanzar y/o regular el ritmo de la jugada.'),
            Criterion('1.4', 'Manejo',
                      'Facilitar la continuidad de la jugada a través de la capacidad para controlar y conservar el balón ante una situación de dificultad. Ya sea por falta de espacio o incomodidad, de frente, espaldas o ante un ángulo ciego respecto al rival.'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', 'Posicionamiento Defensivo',
                      'Identificar los espacios que se deben ocupar en función del posicionamiento de sus compañeros y del peligro que suponen los jugadores rivales cuando se enfrenta a la última línea, de manera que pueda estar en disposición para realizar las funciones previstas en el trabajo de línea y así minimizar riesgos.'),
            Criterion('2.2', 'Perfil Defensivo',
                      'Orientar el cuerpo de manera que, cuando está en el lado activo, pueda inducir al rival al comportamiento deseado a fin de que no progrese el ataque, o que cuando está en el lado débil/contrario, pueda dominar referencias y anticipar la acción.'),
            Criterion('2.3', 'Marcaje Individual',
                      'Mantener proximidad respecto al jugador rival que le corresponde tomar en su zona, para evitar que participe del juego o que reciba el balón con comodidad. Además debe evitar perder las demás referencias del juego y/o dejarse llevar solo por el balón. Dentro de área, en fase de finalización, identificar la marca significa que el rival esté a una distancia máxima de un brazo. En este caso se tiene que mantener la marca individual hasta el final de la acción. Fuera del área, en fase de progresión, si el defensor está en el lado activo o fuerte, identificar la marca significa que el atacante rival esté a una distancia máxima de dos brazos. En este caso se tiene que mantener la marca mientras el rival esté en la zona del defensor.'),
            Criterion('2.4', '1v1 Defensivo',
                      'Evitar que el rival le supere y progrese (ej. y lance centro), ya sea robando el balón de manera que recupera la posesión, interrumpiendo la acción u obligando a retardar el ataque rival.'),
            Criterion('2.5', 'Anticipo',
                      'Dominar las distancias respecto al rival, perfil y lectura de trayectorias, a fin de que cuando en ventaja, se pueda evitar que el receptor rival contacte el balón, ya sea interrumpiendo la acción rival o recuperando la posesión del balón.'),
            Criterion('2.6', 'Defender Espacios Amplios',
                      'Eliminar o reducir el peligro de la acción rival, gracias su posicionamiento, anticipo, perfil y 1v1 defensivo, cuando se encuentra lejos del arco propio y con escasas posibilidades de recibir ayudas de compañeros.'),
            Criterion('2.7', 'Defender Hacia Delante',
                      'Asumir iniciativa cuando en ventaja, dando pasos hacia delante para inducir al poseedor del balón al comportamiento deseado, limitar sus opciones y mantener el bloque corto.'),
            Criterion('2.8', 'Transiciones tras Recuperar',
                      'Cambiar mentalidad de forma agresiva para que en caso de tener la posesión, progresar mediante conducción o pase, o mantener la posesión a través de un pase o conducción de seguridad a zona libre. En caso de no tener la posesión, dar una línea vertical, caso contrario, ofrecer línea de seguridad.'),
            Criterion('2.9', 'Ritmo Defensivo',
                      'Repetir acciones defensivas de manera continua, sin pararse: posicionamiento, marcaje, anticipo, defensa hacia delante y transiciones.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', '1v1 Ofensivo',
                      'Superar rival ante duelo individual, ya sea por velocidad o habilidad/drible, a fin de ofrecer continuidad a la acción ofensiva del equipo.'),
            Criterion('3.2', 'Vigilancia y Acompañamiento',
                      'Ocupar un espacio que le permite mantener el bloque corto, en disposición de participar y de controlar a posibles receptores rivales cuando el equipo progresa. Suele ser en campo rival.'),
            Criterion('3.3', 'Transiciones tras Pérdida',
                      'Cambiar mentalidad de forma agresiva para, en caso de estar cercano al balón, presionar rápido sobre poseedor y en caso de estar alejado, proteger el eje y evitar progresión del rival.'),
            Criterion('3.4', 'Incorporación Ofensiva',
                      'Detectar momentos y espacios para incorporarse en zonas profundas, ya sea por dentro o fuera, con y sin balón. Ante la posibilidad de incorporarse con balón y generar mayores ventajas, debe evitar jugar/lanzar desde la posición.'),
            Criterion('3.5', 'Efectividad Ofensiva',
                      'Desequilibrar y concretar jugadas en etapa de pre finalizacióńn y finalizacióńn que impacten de manera favorable en el juego y en el resultado.'),
            Criterion('3.6', 'Ritmo Ofensivo',
                      'Repetir de acciones, no pararse, a fin de ofrecer constantemente opciones de juego: 1v1, vigilancias y acompañamiento, transiciones e incorporaciones.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Resistencia Alta Intensidad',
                      'Repetir esfuerzos de alta intensidad de manera sostenida a lo largo del partido.'),
            Criterion('4.2', 'Agilidad',
                      'Cambiar de dirección, arrancar, parar y responder a estímulos en el menor tiempo posible.'),
            Criterion('4.3', 'Velocidad',
                      'Cubrir una distancia dada, en el menor tiempo posible.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Concentración',
                      'Fijar y mantener atención de forma oportuna en los aspectos más relevantes del juego'),
            Criterion('5.2', 'Combativo/Competitivo',
                      'Desear de forma continua imponerse y superar al rival, especialmente ante condiciones adversas, y siempre dentro del marco de las reglas del juego.'),
        )),
        ),
        build='Atlético',
        height='Mediano',
    ),
    Profile(
        'medio_centro', 'Medio Centro',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Control',
                      'Dominar el balón tras recepción, especialmente en espacios cortos y entre líneas, de manera que le genere una ventaja para poder dar así continuidad al juego, mediante: atraer, ir a fijar un rival, pasar, ganar metros, o superar una línea.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón en tiempo y forma a diferentes distancias, de manera que el compañero reciba con ventaja. Especialmente en construcción, a fin de que el receptor pueda dar progresión directa o vía tercer hombre.'),
            Criterion('1.3', 'Conducción',
                      'Trasladar el balón pegado al pie y con cabeza levantada, manteniendo la posesión, a fin de poder fijar, superar una línea/avanzar y/o regular el ritmo de la jugada.'),
            Criterion('1.4', 'Manejo',
                      'Facilitar la continuidad de la jugada a través de la capacidad para controlar y conservar el balón ante una situación de dificultad. Ya sea por falta de espacio o incomodidad, de frente, espaldas o ante un ángulo ciego respecto al rival.'),
            Criterion('1.5', 'Juego Aéreo',
                      'Disputar y ganar duelos aéreo, y dirigir el balón a un objetivo deseado.'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', '1v1 Defensivo',
                      'Evitar que el rival le supere y progrese, ya sea robando el balón de manera que recupera la posesión, interrumpiendo la acción, u obligando a retardar el ataque rival.'),
            Criterion('2.2', '2do Balón',
                      'Ganar segundas opciones a través de un buen posicionamiento producto de la lectura de trayectoria del balón y de agresividad en la participación, cuando por función propia de la posición, le corresponde estar cercano a balón.'),
            Criterion('2.3', 'Profundidad Defensiva',
                      'Mantener balance en fase defensiva y transiciones tras pérdida, respecto a la última línea, conservando el bloque corto, ocupando el eje y espacios vulnerables ante el ataque rival. De manera que, cuando la función propia del rol lo exija, esté en disposición para compensar en la última línea, ya sea porque existe un intervalo excesivo o porque un compañero ha sido superado.'),
            Criterion('2.4', 'Defender Espacios Amplios',
                      'Eliminar o reducir el peligro de la acción rival, gracias su posicionamiento, anticipo, perfil y 1v1 defensivo, cuando se encuentra lejos del arco propio y con escasas posibilidades de recibir ayudas de compañeros.'),
            Criterion('2.5', 'Equilibrio Defensivo',
                      'Mantener balance respecto a la línea posterior en fase defensiva y transiciones tras pérdida, conservando el bloque el corto, ocupando el eje y ocupando espacios vulnerables ante el ataque rival. De manera que, cuando la función propia del rol lo exija, esté en disposición para compensar espacio/zona abandonada por un compañero o para compensar un espacio ante transición ofensiva rival'),
            Criterion('2.6', 'Ritmo Defensivo',
                      'Repetir acciones defensivas de manera continua, sin pararse: 2do balón, profundidad defensiva, equilibrios defensivos.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', 'Vista Previa',
                      'Observar el entorno previo a recibir el balón, para adquirir información de compañeros, rivales y espacios, a fin de poder anticipar la toma de decisión.'),
            Criterion('3.2', 'Búsqueda de Progresión',
                      'Superar líneas rivales, ya sea mediante un pase, conducción o atracción, que producto de una correcta lectura del juego, facilita la continuidad vertical en la acción posterior del equipo'),
            Criterion('3.3', 'Líneas de pases',
                      'Ocupar espacios en función de su ubicación respecto al balón y de la estructura del equipo, cuando está dentro del campo visual y a distancia prudente del poseedor, a fin de ofrecer líneas de pase limpias y en cuadrantes desocupados, que le otorguen ventaja en caso de recibir, ya sea a espaldas de la línea de presión rival o en su lado debil. Que no facilite el marcaje y que ofrezca facilidades al poseer del balón.'),
            Criterion('3.4', 'Vigilancia y Acompañamiento',
                      'Ocupar un espacio que le permite mantener el bloque corto, en disposición para participar y controlar a posibles receptores rivales cuando el equipo ataca. Suele ser en campo rival.'),
            Criterion('3.5', 'Ritmo Ofensivo',
                      'Repetir de acciones, no pararse, a fin de ofrecer constantemente opciones de juego: búsqueda de progresión, líneas de, llegadas a finalización.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Potencia',
                      'Imponer presencia física en el cuerpo a cuerpo para mantener o recuperar la posición en una disputa, producto de la fuerza, velocidad y uso adecuado del cuerpo.'),
            Criterion('4.2', 'Velocidad Gestual',
                      'Ejecutar en el menor tiempo posible los gestos técnicos que demenda una acción. Por ejemplo, la velocidad para sacar un pase.'),
            Criterion('4.3', 'Agilidad',
                      'Cambiar de dirección, arrancar, parar y responder a estímulos en el menor tiempo posible.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Liderazgo',
                      'Contribuir a que el equipo alcance su objetivo, a través de su capacidad para influir en compañeros, de una comunicación asertiva y deseo del balón'),
            Criterion('5.2', 'Concentración',
                      'Fijar y mantener atención de forma oportuna en los aspectos más relevantes del juego'),
            Criterion('5.3', 'Combativo/Competitivo',
                      'Desear de forma continua imponerse y superar al rival, especialmente ante condiciones adversas, y siempre dentro del marco de las reglas del juego.'),
        )),
        ),
        build='Atlético',
        height='Alto',
    ),
    Profile(
        'interior', 'Interior',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Controles',
                      'Dominar el balón tras recepción, especialmente en espacios cortos y entre líneas, de manera que le genere una ventaja, para poder dar así continuidad al juego: atraer, ir a fijar un rival, pasar, ganar metros, o superar una línea.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón en tiempo y forma a diferentes distancias, de manera que el compañero reciba con ventaja. Especialmente en acciones de último o penúltimo pase (ej. pase a extremo previo a centro) en finalización.'),
            Criterion('1.3', 'Conducción',
                      'Trasladar el balón pegado al pie y con cabeza levantada, manteniendo la posesión, a fin de poder fijar, superar una línea/avanzar y/o regular el ritmo de la jugada.'),
            Criterion('1.4', 'Manejo',
                      'Facilitar la continuidad de la jugada a través de la capacidad para controlar y conservar el balón ante una situación de dificultad. Ya sea por falta de espacio o incomodidad, de frente, espaldas o ante un ángulo ciego respecto al rival.'),
            Criterion('1.5', 'Remate',
                      'Finalizar jugadas en forma, con ambas piernas y diferentes superficies del pie.'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', 'Cerrar Línea de Pase',
                      'Valorar espacios que se deben ocupar en funcion del peligro que supone el ataque rival para la propia puerta, posibles receptores y compañeros, a fin de interceptar trayectoria del balón. En caso de estar cerca a balón, acosar al poseedor para disuadir progresión o evitar que juegue con compañero rival de mayor ventaja. En caso de estar alejado a balón, ubicarse en una posición para disuadir que se intente dar un pase al rival más aventajado en su zona de influencia; y en caso de pase, intentar anticipar o que rival reciba en desventaja.'),
            Criterion('2.2', 'Defender hacia delante',
                      'Asumir iniciativa cuando en ventaja, dando pasos hacia delante para inducir al poseedor del balón al comportamiento deseado, limitar sus opciones y mantener el bloque corto.'),
            Criterion('2.3', 'Transiciones tras Recuperar',
                      'Cambiar mentalidad de forma agresiva para que en caso de tener la posesión, progresar mediante conducción o pase, o mantener la posesión a través de un pase o conducción de seguridad a zona libre. En caso de no tener la posesión, dar una línea vertical, caso contrario, ofrecer línea de seguridad.'),
            Criterion('2.4', 'Ritmos Defensivos',
                      'Repetir acciones defensivas de manera continua, sin pararse: cierre de líneas de pase, defender hacia delante, transiciones tras recuperar.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', 'Perfil Ofensivo',
                      'Orientar el cuerpo de manera que permita recoger información de las referencias del juego a fin de ponerse en disposición de dar progresión rápida.'),
            Criterion('3.2', 'Vista Previa',
                      'Observar el entorno previo a recibir el balón, para adquirir información de compañeros, rivales y espacios, a fin de poder anticipar la toma de decisión.'),
            Criterion('3.3', 'Línea de Pase',
                      'Ocupar espacios en función de su ubicación respecto al balón y de la estructura del equipo, cuando está dentro del campo visual y a distancia prudente del poseedor, a fin de ofrecer líneas de pase limpias y en cuadrantes desocupados, que le otorguen ventaja en caso de recibir, ya sea a espaldas de la línea de presión rival o en su lado debil. Que no facilite el marcaje y que ofrezca facilidades al poseer del balón.'),
            Criterion('3.4', 'Búsqueda de Progresión',
                      'Superar líneas rivales, ya sea mediante un pase, conducción o atracción, que producto de una correcta lectura del juego, facilita la continuidad vertical en la acción posterior del equipo.'),
            Criterion('3.5', 'Transición tras Pérdida',
                      'Cambiar mentalidad de forma agresiva para, en caso de estar cercano al balón, presionar rápido sobre poseedor y en caso de estar alejado, proteger el eje y evitar progresión del rival.'),
            Criterion('3.6', 'Llegada a Finalización',
                      'Entrar desde 2da línea para aprovechar oportunidades de terminar jugadas. Ante pregresión por el pasillo exterior lejano para centro, entrar para ocupar intervalos que se generan en relación con el central alejado o el espacio que se genera en la frontal del área cuando la defensa se hunde. Antre progresión por pasillo central y disponibilidad de intervalo entre central y lateral alejado, ofrecer línea de pase al espacio.'),
            Criterion('3.7', 'Efectividad Ofensiva',
                      'Desequilibrar y concretar jugadas en etapa de pre finalización y finalización que impacten de manera favorable en el juego y en el resultado'),
            Criterion('3.8', 'Ritmos Ofensivos',
                      'Repetir de acciones, no pararse, a fin de ofrecer constantemente opciones de juego: líneas de pase, transiciones, llegadas a finalización.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Resistencia Alta Intensidad',
                      'Repetir esfuerzos de alta intensidad de manera sostenida a lo largo del partido'),
            Criterion('4.2', 'Velocidad Gestual',
                      'Ejecutar en el menor tiempo posible los gestos técnicos que demenda una acción. Por ejemplo, para sacar un remate.'),
            Criterion('4.3', 'Agilidad',
                      'Cambiar de dirección, arrancar, parar y responder a estímulos en el menor tiempo posible.'),
            Criterion('4.4', 'Potencia',
                      'Imponer presencia física en el cuerpo a cuerpo para mantener o recuperar la posición en una disputa, producto de la fuerza, velocidad y uso adecuado del cuerpo.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Concentración',
                      'Fijar y mantener atención de forma oportuna en los aspectos más relevantes del juego'),
            Criterion('5.2', 'Combativo/Competitivo',
                      'Desear de forma continua imponerse y superar al rival, especialmente ante condiciones adversas, siempre dentro del marco de las reglas del juego.'),
        )),
        ),
        build='Atlético / Delgado',
        height='Indiferente',
    ),
    Profile(
        'extremo', 'Extremo',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Controles',
                      'Dominar el balón tras recepción, especialmente para ganar metros y/o superar rival cuando tiene un espacio por delante, de manera que le permita atacar la portería contraria y dar continuidad al juego.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón en tiempo y forma a diferentes distancias, de manera que el compañero reciba con ventaja. Especialmente en acciones de finalización a través de centros.'),
            Criterion('1.3', 'Conducción',
                      'Trasladar el balón pegado al pie y con cabeza levantada, manteniendo la posesión, a fin de poder fijar, superar una línea/avanzar y/o regular el ritmo de la jugada.'),
            Criterion('1.4', 'Manejo',
                      'Facilitar la continuidad de la jugada a través de la capacidad para controlar y conservar el balón ante una situación de dificultad. Ya sea por falta de espacio o incomodidad, de frente, espaldas o ante un ángulo ciego respecto al rival.'),
            Criterion('1.5', 'Remate',
                      'Finalizar jugadas en forma, con ambas piernas y diferentes superficies del pie. Especialmente para cuando llega por el lado contrario.'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', 'Presión',
                      'Evitar progresión en inicio de juego rival. En caso de ser el cercano, inducir al poseedor del balón al comportamiento deseado y limitar sus opciones mediante acoso, o de ser posible, entrar y robar. En caso de ser alejado, cerrar líneas de pase, protegiendo el eje.'),
            Criterion('2.2', '1v1 Defensivo',
                      'Evitar que el rival le supere y progrese, ya sea robando el balón de manera que recupera la posesión, interrumpiendo la acción, u obligando a retardar el ataque rival.'),
            Criterion('2.3', 'Agrupación',
                      'Replegar, en caso de que su línea sea superada, para ayudar al equipo a defender en superioridad numérica y/o posicional, y mantener el bloque compacto.'),
            Criterion('2.4', 'Retornos',
                      'Perseguir al oponente directo (lateral) hacia campo propio, ya sea si tiene la posesión del balón o si es un posible receptor, a fin de evitar que participe en ataque rival.'),
            Criterion('2.5', 'Transiciones tras Recuperar',
                      'Cambiar mentalidad de forma agresiva para que en caso de tener la posesión, progresar mediante conducción o pase, o mantener la posesión a través de un pase o conducción de seguridad a zona libre. En caso de no tener la posesión, dar una línea vertical, caso contrario, ofrecer línea de seguridad.'),
            Criterion('2.6', 'Ritmos Defensivos',
                      'Repetir acciones defensivas de manera continua, sin pararse: presiones, agrupaciones, retornos, transiciones.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', '1v1 Ofensivo',
                      'Superar rival ante duelo individual, ya sea por velocidad o habilidad/drible, a fin de ofrecer continuidad a la acción ofensiva del equipo.'),
            Criterion('3.2', 'Amplitud',
                      'Ocupar espacios en función de su ubicación respecto al balón y de la estructura del equipo, para generar amplitud u ofrecer líneas de pase que le otorguen ventaja en caso de recibir, ya sea a espaldas de la línea de presión rival o en su lado debil. Que no facilite el marcaje, que ofrezca facilidades al poseer del balón y que facilite progresión.'),
            Criterion('3.3', 'Desmarque / Atacar Espacio',
                      'Escapar de la vigilancia rival a través de movimientos hacia espacios libres, disponibles o auto generados, ya sea en apoyo, o en ruptura de forma agresiva hacia la portería, a espaldas de la última línea defensiva rival.'),
            Criterion('3.4', 'Transición tras Pérdida',
                      'Cambiar mentalidad de forma agresiva para, en caso de estar cercano al balón, presionar rápido sobre poseedor y en caso de estar alejado, proteger el eje y evitar progresión del rival.'),
            Criterion('3.5', 'Efectividad Ofensiva',
                      'Desequilibrar y concretar jugadas en etapa de pre finalizacióńn y finalización que impacten de manera favorable en el juego y en el resultado.'),
            Criterion('3.6', 'Ritmos Ofensivos',
                      'Repetir acciones ofensivas de manera continua, sin pararse, a fin de ofrecer continuidad al juego: 1v1, desmarques y transiciones.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Resistencia Alta Intensidad',
                      'Repetir esfuerzos de alta intensidad de manera sostenida a lo largo del partido.'),
            Criterion('4.2', 'Velocidad',
                      'Cubrir una distancia dada, en el menor tiempo posible.'),
            Criterion('4.3', 'Agilidad',
                      'Cambiar de dirección, arrancar, parar y responder a estímulos en el menor tiempo posible.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Confianza/ Valentía',
                      'Asumir riesgos medidos y controlar emociones que afectan de manera negativa el rendimiento. Por ejemplo, superar con facilidad un error individual y mantenerse presente en el juego.'),
            Criterion('5.2', 'Constancia',
                      'Perseverar en busca de un objetivo, a pesar de las dificultades'),
        )),
        ),
        build='Atlético o Delgado',
        height='Indiferente',
    ),
    Profile(
        'centro_delantero', 'Centro Delantero',
        (
        Section(1, '1. TÉCNICA', (
            Criterion('1.1', 'Controles',
                      'Dominar el balón tras recepción, especialmente para ganar metros y/o superar rival cuando tiene un espacio por delante, de manera que le permita atacar la portería contraria y dar continuidad al juego.'),
            Criterion('1.2', 'Pase',
                      'Dirigir el balón en tiempo y forma a diferentes distancias, de manera que el compañero reciba con ventaja. En progresión, a fin de que pueda asociarse con facilidad (ej. con interior que viene de frente a modo de tercer hombre). En finalización, a fin de que pueda dar progresión directa a través de último o penúltimo pase (ej. a extremo que rompe al espacio para luego centrar).'),
            Criterion('1.3', 'Juego Aéreo',
                      'Disputar y ganar duelos aéreo y dirigir el balón a un objetivo deseado, tanto en defensa como en finalización.'),
            Criterion('1.4', 'Manejo',
                      'Facilitar la continuidad de la jugada a través de la capacidad para controlar y conservar el balón ante una situación de dificultad. Ya sea por falta de espacio o incomodidad, de frente, espaldas o ante un ángulo ciego respecto al rival.'),
            Criterion('1.5', 'Remate',
                      'Finalizar jugadas en forma, con ambas piernas y diferentes superficies del pie'),
        )),
        Section(2, '2. TÁCTICA DEFENSIVA', (
            Criterion('2.1', 'Presión',
                      'Evitar progresión en inicio de juego rival. En caso de ser el cercano, inducir al poseedor del balón al comportamiento deseado y limitar sus opciones mediante acoso, o de ser posible, entrar y robar. En caso de ser alejado, cerrar líneas de pase, protegiendo el eje.'),
            Criterion('2.2', 'Agrupación',
                      'Replegar, en caso de que su línea sea superada, para ayudar al equipo a defender en superioridad numérica y/o posicional, y mantener el bloque compacto.'),
            Criterion('2.3', 'Transición Tras Recuperar',
                      'Cambiar mentalidad de forma agresiva para que en caso de tener la posesión, progresar mediante conducción o pase, o mantener la posesión a través de un pase o conducción de seguridad a zona libre. En caso de no tener la posesión, dar una línea vertical, caso contrario, ofrecer línea de seguridad.'),
        )),
        Section(3, '3. TÁCTICA OFENSIVA', (
            Criterion('3.1', '1v1 Ofensivo',
                      'Superar rival ante duelo individual, ya sea por velocidad o habilidad/drible; a fin de ofrecer continuidad a la acción ofensiva del equipo'),
            Criterion('3.2', '2do Balón Ofensivo',
                      'Ganar segundos balones a través de un buen posicionamiento, de la lectura de trayectoria del balón y de agresividad en la participación.'),
            Criterion('3.3', 'Transición tras Pérdida',
                      'Cambiar mentalidad de forma agresiva para, en caso de estar cercano al balón, presionar rápido sobre poseedor y en caso de estar alejado, proteger el eje y evitar progresión del rival.'),
            Criterion('3.4', 'Autonomía',
                      'Resolver por si mismo con efectividad, en situaciones de juego en las que no cuenta con apoyo de compañeros (a más de un cuadrante de por medio), ya sea para finalizar o para sostener el balón hasta que el equipo se incorpore o restructure. Incluye ganar una falta.'),
            Criterion('3.5', 'Atacar Espacio',
                      'Resolver por si mismo con efectividad, en situaciones de juego en las que no cuenta con apoyo de compañeros (a más de un cuadrante de por medio), ya sea para finalizar o para sostener el balón hasta que el equipo se incorpore o restructure. Incluye ganar una falta.)'),
            Criterion('3.6', 'Llegadas a Finalización',
                      'Llegar a zonas de finalización desde 2da línea y por detrás de la línea del balón, cuando sale de su posición ya sea en apoyo o para participar en fase progresión.'),
            Criterion('3.7', 'Diagonales de Finalización',
                      'Carreras diagonales hacia dentro o fuera para ofrecer opción de pase en fase de finalización. Se deben priorizar carreras trasversales a la trayectoria del balón'),
            Criterion('3.8', 'Superioridad Posicional',
                      'Realizar movimientos que le permitan ganar tiempo y espacio para finalizar: separarse, fijar, atacar desde atrás, posicionarse en puntos ciegos de marca, comunicar de manera gestual y corporal (por sobre verbal). Puede ser producto de estar libre de marca o de llegar, tras ganar la portería al marcador.'),
            Criterion('3.9', 'Efectividad Ofensiva',
                      'Desequilibrar y concretar jugadas en etapa de pre finalizacióńn y finalizacióńn que impacten de manera favorable en el juego y en el resultado. Gol'),
            Criterion('3.10', 'Ritmo Ofensivo',
                      'Repetir acciones ofensivas de manera continua, sin pararse, a fin de ofrecer continuidad al juego: 1v1, 2do balón, transición, atacar espacios, llegar a finalizar y diagonales de finalización.'),
        )),
        Section(4, '4. CONDICIONAL', (
            Criterion('4.1', 'Velocidad Gestual',
                      'Ejecutar en el menor tiempo posible los gestos técnicos que demanda una acción. Por ejemplo, la velocidad para sacar un remate.'),
            Criterion('4.2', 'Potencia',
                      'Imponer presencia física en el cuerpo a cuerpo para mantener o recuperar la posición en una disputa, producto de la fuerza, velocidad y uso adecuado del cuerpo.'),
        )),
        Section(5, '5. MENTAL (COGNITIVO Y VOLITIVO)', (
            Criterion('5.1', 'Combativo/Competitivo',
                      'Desear de forma continua imponerse y superar al rival, especialmente ante condiciones adversas, siempre dentro del marco de las reglas del juego.'),
            Criterion('5.2', 'Confianza/ Valentía',
                      'Asumir riesgos medidos y controlar emociones que afectan de manera negativa el rendimiento. Por ejemplo, superar con facilidad un error individual y mantenerse presente en el juego.'),
        )),
        ),
        build='Atlético',
        height='Alto o Mediano',
    ),
)

_BY_KEY = {p.key: p for p in PROFILES}

# Canonical role → the profile a player in that role is scored on by default.
# The scout can pick another profile on the sheet, or none (a single 1–5 rating).
ROLE_PROFILES: dict[str, str] = {
    "Portero": "arquero",
    "Defensa central": "defensa_central",
    "Lateral izquierdo": "lateral",
    "Lateral derecho": "lateral",
    "Pivote": "medio_centro",
    "Mediocentro": "medio_centro",
    "Mediocentro ofensivo": "interior",
    "Mediapunta": "interior",
    "Extremo izquierdo": "extremo",
    "Extremo derecho": "extremo",
    "Delantero centro": "centro_delantero",
}


def get_profile(key: str | None) -> Profile | None:
    return _BY_KEY.get(key or "")


def default_profile(position: str | None) -> Profile | None:
    """The profile a free-text position is scored on, or None (goalkeepers, or a
    position too vague to name a role)."""
    role = canonical_position(position)
    if role is None:
        return None
    return get_profile(ROLE_PROFILES.get(role.role))


def clean_scores(profile: Profile, raw: dict) -> dict[str, int]:
    """Keep only this profile's criteria with a whole score in 1–5.

    Anything else — an unknown code, a blank, "7", "abc" — is dropped rather than
    stored, so a stored sheet is always valid for its profile."""
    scores: dict[str, int] = {}
    for code, value in raw.items():
        if code not in profile.codes:
            continue
        try:
            score = int(str(value).strip())
        except (TypeError, ValueError):
            continue
        if SCORE_MIN <= score <= SCORE_MAX:
            scores[code] = score
    return scores


def match_rating(scores: dict[str, int]) -> float | None:
    """The single 1–5 rating a score sheet stands for: the mean of every scored
    criterion, kept to two decimals.

    Deliberately not rounded to one decimal: 3.46 → 3.5 would flip the derived
    decision from "Interesante" to "Muy interesante" (see decision_for_rating)."""
    if not scores:
        return None
    return round(sum(scores.values()) / len(scores), 2)


def average(values) -> Decimal | None:
    """A report average the client's way: one decimal, rounded half-up."""
    values = [v for v in values if v is not None]
    if not values:
        return None
    exact = Decimal(sum(values)) / Decimal(len(values))
    return exact.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)


def format_average(value: Decimal | None) -> str:
    """"3,7" — the comma decimal the client's report uses; "—" when empty."""
    return "—" if value is None else f"{value}".replace(".", ",")


def is_complete(profile: Profile, scores: dict[str, int]) -> bool:
    return profile.codes <= scores.keys()
