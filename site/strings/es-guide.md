# Spanish wording: style and glossary

For whoever writes or checks `es.po`, the Spanish of every Publick site. The
first readers are in Lawrence, Massachusetts, where most Spanish speakers are
Dominican or Puerto Rican, so the Spanish is the plain Spanish they read
every day: not formal, not word for word from the English, and not from
Spain.

## Voice

- **Plain and direct**, about an 8th-grade reading level, like the English.
  Short sentences. Say what the English means, not what each word says.
- **Address the reader as *usted*** ("Busque", "su distrito"), as US public
  services in Spanish do. It's polite without being stiff. (A reviewer may
  prefer *tú*; if so, change it everywhere at once.)
- **Neutral Latin American vocabulary.** No *vosotros*, and no words only
  Spain uses (*ordenador*, *móvil*, *coger*). Prefer words Caribbean readers
  use: *celular*, *carro*, *alquiler*.
- **Neutral about the city**, as the English is: no judgment of how well the
  city does.
- Keep the English's sentence order only when it reads naturally in Spanish.

## Mechanics

- Keep every placeholder exactly: `%(name)s`, `{name}`, `%%` (a percent
  sign), and all HTML (`<a href="%(url)s">…</a>`, `<strong>`, `<time …>`).
  Move them where Spanish needs them. The tests check that each translation
  has the same placeholders as its English.
- A board's name inserted into a sentence (`%(title)s`, `%(board)s`, `%(body)s`)
  may be Spanish with English in parentheses or plain English, so don't put an
  article or *de* right before it: *%(title)s: reunión*, not *Reunión de %(title)s*.
- Plurals have two forms, as in English (one, and everything else).
- Dates: *jueves, 1 de octubre de 2026*; *1 de octubre de 2026*; *octubre de
  2026*. Months and weekdays in lower case. Short forms: *jue*, *oct*
  (*jue, 1 oct*).
- Times: *7:00 p. m.*, *9:30 a. m.*
- Numbers and money as in the US, as Puerto Rico and the Dominican Republic
  write them: *1,234*, *4.8%*, *$140.6 millones*, *$9,502*.
- Quotation marks: “ ”.
- Proper names stay as they are: *Publick*, *SeeClickFix*, *Zoom*, *MCAS*,
  *GoatCounter*, *OpenStreetMap*. Agencies get their Spanish name when it's
  common, with the English acronym (*la Oficina del Censo de EE. UU.*, *la
  Oficina de Estadísticas Laborales (BLS)*); otherwise the English name.
- "AI" is *IA* (*inteligencia artificial* the first time on a page, where the
  English says "AI" in a sentence about how summaries are made).

## Glossary

| English | Spanish |
|---|---|
| meeting | reunión |
| board / committee / commission | junta / comité / comisión |
| City Council | Concejo Municipal (a town's board names are in its config, not here) |
| councilor | concejal, concejala |
| Mayor | alcalde, alcaldesa |
| agenda | agenda |
| minutes | actas (plural: *las actas*) |
| public hearing | audiencia pública |
| public comment | comentarios del público |
| vote / motion | votación / moción |
| decision / recommendation | decisión / recomendación |
| executive session | sesión ejecutiva (a puerta cerrada) |
| summary | resumen |
| scanned document | documento escaneado |
| 311 request | solicitud al 311 |
| acknowledged (a 311 request) | confirmada: the city confirmed it received it |
| submitted (a 311 request) | enviada (*del envío a…*) |
| time to acknowledge / time to close | tiempo de confirmación / tiempo de cierre (*typical*: tiempo típico de…) |
| Not enough data | Datos insuficientes (the same where it's quoted) |
| longest-open requests | solicitudes abiertas desde hace más tiempo |
| no update in over a year | sin actualizar en más de un año |
| open / closed (a request) | abierta / cerrada |
| ward / precinct | distrito / precinto |
| who represents you | quién lo representa |
| fiscal year | año fiscal |
| property tax / tax bill | impuesto a la propiedad / factura del impuesto a la propiedad |
| single-family home | casa unifamiliar |
| budget / general fund | presupuesto / fondo general |
| free cash | efectivo disponible (*free cash*) |
| stabilization fund | fondo de estabilización |
| levy limit / Proposition 2½ | límite de recaudación / Proposición 2½ |
| bond rating | calificación crediticia |
| graduation rate | tasa de graduación |
| chronic absenteeism | ausentismo crónico |
| English Language Arts / Math | Artes del Lenguaje en Inglés / Matemáticas |
| per pupil | por estudiante |
| building permit | permiso de construcción |
| homes (housing units) | viviendas |
| rent / home value | alquiler / valor de la vivienda |
| affordable housing | vivienda asequible |
| Subsidized Housing Inventory | Inventario de Vivienda Subsidiada (SHI) |
| unemployment rate | tasa de desempleo |
| data / source | datos / fuente |
| Mass. Division of Local Services (DLS) | la División de Servicios Locales (DLS) de Massachusetts; *DLS de Massachusetts* in short labels |
| state test | examen estatal (*los exámenes MCAS*) |
| compared with the state | en comparación con el estado |
| alerts (Notify Me) | avisos |
| written / transcribed by AI | escrito / transcrito con IA |
| add (text will be added) | agregar, not *añadir* |
| up to date / behind | al día / atrasado |
| report a problem | informar un problema |
