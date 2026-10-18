# 🥋 TATAMI: DŌ — Edición 2026 · `Tatamydo.py`

> Doce luchadores. Un dojo al atardecer. La luz se va apagando asalto a asalto.

Juego de lucha 2.5D, uno contra uno, en Python puro con pygame. Todo es procedimental:
no hay ficheros de arte ni de audio. El dojo Kōyō-kan, los doce luchadores, la tinta sumi-e,
el taiko y el shakuhachi se generan en el arranque.

`Tatamydo.py` es el lanzador; el juego vive en el paquete `tatamido/`:

| Módulo | Qué hace |
|---|---|
| `config.py` · `data.py` | Constantes, presets gráficos, rigs de luz por asalto, los doce luchadores |
| `inputs.py` · `moves.py` | Teclado/mando con buffer de 7 frames, comandos clásicos, frame data por luchador |
| `fighter.py` | Máquina de estados, cancelaciones en golpe, Kiai Final, IA con personalidad |
| `poses.py` · `render_fighter.py` | Esqueleto procedimental, ropa, luz lateral, contornos, estelas |
| `dojo.py` · `fx.py` · `postfx.py` | Kōyō-kan, haces, polvo, lluvia, tinta sumi-e, grano, LUTs, bloom |
| `match.py` · `replay.py` | Triángulo, puntuación, fase de suelo, repetición de ippon, grabación determinista |
| `game.py` | Escenas, menús, modos, Modo Foto, opciones y accesibilidad |
| `tests/` | `pytest`: determinismo, triángulo, parada perfecta, jogai, buffer/cancel, repeticiones |

## Ejecutar

```bash
pip install pygame-ce numpy          # numpy es opcional: sin él, el juego es mudo
python Tatamydo.py                   # ventana 1280×720 (F11: pantalla completa)
python Tatamydo.py --preset cine     # portatil | bajo | medio | alto | ultra | cine
python Tatamydo.py --photo-mode      # arranca directo en el Modo Foto
python Tatamydo.py --ai-vs-ai        # espectador
python Tatamydo.py --debug-hitboxes
python -m pytest -q                  # tests de simulación
```

## Controles

| Acción | J1 | J2 | Mando |
|---|---|---|---|
| Mover / saltar / agacharse | `WASD` | Flechas | Stick / cruceta |
| Golpe ligero · medio · fuerte | `U` `I` `O` | `Num 4` `5` `6` | X · Y · RB |
| Agarre | `J` | `Num 1` | A |
| Bloqueo (parada perfecta: 4 frames) | `K` | `Num 2` | B |
| Kiai Final (con la barra llena) | `L` | `Num 3` | RT |
| Técnica de estilo | Atrás + Medio (moderno) · ↓↘→ + Medio (clásico) | | |
| Barrido / golpe aéreo | Agachado + Fuerte · Salto + golpe | | |
| Modo Foto · pausa · hitboxes · frames | `F12` · `Esc` · `F1` · `F2` | | |

## Qué hay dentro

- **Triángulo** golpe → agarre → bloqueo → golpe, parada perfecta con congelado de 8 frames,
  aliento (agotado: jadeo y −40 % de daño), barra de Kiai, hitstop, zoom de impacto y cámara lenta.
- **Sensación de juego:** buffer de entrada de 7 frames (la pulsación en la recuperación no se
  pierde), cancelaciones en golpe (ligero → medio/fuerte/agarre, medio → fuerte/especial),
  escalado de daño por combo con contador en pantalla, estelas en los golpes fuertes.
- **Determinismo y repeticiones:** la lógica usa un generador propio por combate; cada combate
  terminado se graba en `./replays` (seed + máscara de botones por frame) y se reproduce desde el
  menú *Repeticiones*. Un test garantiza que grabación y directo producen el mismo estado.
- **IA** con cuatro niveles (Fácil a Maestro), consciente del borde del tatami, que castiga
  aturdimientos y usa su Kiai Final.
- **Puntuación japonesa:** waza-ari, ippon (con repetición automática en tres ángulos),
  jogai ×3, desempate por vitalidad y waza-ari. Asalto 4 bajo la lluvia.
- **Fase de suelo** para Sambo, BJJ y lucha: minijuego de tensión con cámara cerrada y viñeta.
- **Doce Kiai Finales** distintos (ráfagas, proyecciones, tornado, oshidashi, gancho al hígado,
  Kokyū Nage pasivo de Okabe que devuelve cada parada perfecta).
- **Iluminación narrativa** por asalto: tarde dorada, hora azul y noche de farolillos; haces
  volumétricos, polvo en suspensión, sombra del pino sobre los shōji, hoja de arce que cae en el
  último asalto, público de 14 personas que reacciona y aplaude al ippon.
- **Daño visible:** sudor, rojeces en antebrazos, solapa descolocada, cinturón que se afloja.
- **Legibilidad:** siluetas con contorno oscuro, cámara más cerrada, reflejos de los luchadores en
  el tatami barnizado bajo los farolillos, tarjetas de presentación al empezar cada combate.
- **Modos:** Versus local, Versus IA, Arcade con epílogos, Torneo (desbloquea al Sensei Okabe si
  no pierdes un asalto), Entrenamiento con datos de frames, Espectador.
- **Modo Foto:** encuadre libre, zoom, exposición, bokeh, grano y LUTs *Caravaggio*, *Hammershøi*,
  *Kurosawa B/N* y *Daido Moriyama*; exporta PNG con supermuestreo 2× a `./fotos`.
- **Accesibilidad:** reducción de destellos, temblor de cámara desactivable, alto contraste y
  filtros de daltonismo. Progreso y opciones en `~/.tatamydo_save.json`.

Código bajo licencia MIT. Todos los luchadores y el dojo son ficticios.

> *「礼に始まり礼に終わる」 — Empieza con un saludo, termina con un saludo.*
