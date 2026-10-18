"""Interfaz a resolución nativa con coordenadas lógicas.

Todo el código de menús y HUD está escrito para un lienzo de 1280×720. `UISurface` conserva ese contrato
(tamaños, posiciones y grosores en unidades lógicas) pero pinta en una superficie a la resolución de render:
los rectángulos y círculos se rasterizan a escala y el texto se compone con la fuente al tamaño real, no
reescalado desde 720p. Las funciones de `draw` aceptan tanto una `UISurface` como una Surface corriente
(entonces son exactamente `pygame.draw`), de modo que el camino de CPU no cambia.

Además lleva la cuenta de la zona que se ha pintado: borrar y subir a la GPU sólo esa zona (y la del
fotograma anterior) cuesta poco aunque la superficie sea de 4K.
"""
import pygame


def _r(v, k):
    return int(round(v * k))


def _rect(rect, k):
    """Rect lógico → nativo, redondeando bordes (no tamaños) para que piezas contiguas no dejen huecos."""
    x, y, w, h = rect if len(rect) == 4 else (*rect[0], *rect[1])
    x0, y0 = _r(x, k), _r(y, k)
    return pygame.Rect(x0, y0, _r(x + w, k) - x0, _r(y + h, k) - y0)


def _w(width, k):
    return 0 if width <= 0 else max(1, _r(width, k))


class HiText:
    """Texto ya compuesto a resolución nativa; se mide en unidades lógicas."""
    __slots__ = ("img", "k")

    def __init__(self, img, k):
        self.img, self.k = img, k

    def get_width(self):
        return _r(self.img.get_width(), 1 / self.k)

    def get_height(self):
        return _r(self.img.get_height(), 1 / self.k)

    def get_size(self):
        return self.get_width(), self.get_height()

    def get_rect(self, **kw):
        r = pygame.Rect(0, 0, *self.get_size())
        for key, v in kw.items():
            setattr(r, key, v)
        return r

    def set_alpha(self, a, flags=0):
        self.img.set_alpha(a, flags)

    def get_alpha(self):
        return self.img.get_alpha()


class ScaledFont:
    """Fuente al tamaño ×k que devuelve `HiText` y mide en unidades lógicas."""
    __slots__ = ("font", "k")

    def __init__(self, font, k):
        self.font, self.k = font, k

    def render(self, text, antialias=True, color=(255, 255, 255), background=None):
        return HiText(self.font.render(text, antialias, color, background), self.k)

    def size(self, text):
        w, h = self.font.size(text)
        return _r(w, 1 / self.k), _r(h, 1 / self.k)

    def get_height(self):
        return _r(self.font.get_height(), 1 / self.k)

    def get_linesize(self):
        return _r(self.font.get_linesize(), 1 / self.k)


class ScaledFonts:
    """Vista de un `Fonts` para una interfaz a escala k: mismos tamaños lógicos, glifos nativos."""

    def __init__(self, base, k):
        self.base, self.k = base, k
        self.cache = {}

    @property
    def cjk_name(self):
        return self.base.cjk_name

    def get(self, size, serif=False):
        key = (size, serif)
        if key not in self.cache:
            self.cache[key] = ScaledFont(self.base.get(max(1, _r(size, self.k)), serif), self.k)
        return self.cache[key]

    def cjk(self, size):
        key = ("cjk", size)
        if key not in self.cache:
            f = self.base.cjk(max(1, _r(size, self.k)))
            self.cache[key] = ScaledFont(f, self.k) if f else None
        return self.cache[key]

    def at(self, surf):
        return self if getattr(surf, "k", 1) == self.k else self.base.at(surf)


class UISurface:
    """Superficie de interfaz: habla en unidades lógicas (lw×lh), pinta en `surf` (lw·k × lh·k)."""
    STRIPS = 8      # la zona pintada se lleva por franjas: las barras de arriba y el kiai de abajo no arrastran el centro

    def __init__(self, size, k):
        self.k = k
        self.lw, self.lh = size
        self.surf = pygame.Surface((_r(self.lw, k), _r(self.lh, k)), pygame.SRCALPHA)
        self.full = self.surf.get_rect()
        self.sh = -(-self.full.h // self.STRIPS)
        whole = [self.full.clip(0, i * self.sh, self.full.w, self.sh) for i in range(self.STRIPS)]
        self.boxes = list(whole)            # zona pintada en este fotograma, por franja (nativa)
        self.prev = [r.copy() for r in whole]   # y la del anterior: hay que subirla para borrarla en la GPU

    # --- tamaño lógico ---
    def get_width(self):
        return self.lw

    def get_height(self):
        return self.lh

    def get_size(self):
        return self.lw, self.lh

    def get_rect(self, **kw):
        r = pygame.Rect(0, 0, self.lw, self.lh)
        for key, v in kw.items():
            setattr(r, key, v)
        return r

    # --- seguimiento de la zona pintada ---
    def _mark(self, r):
        r = r.clip(self.full)
        if r.w and r.h:
            sh, boxes = self.sh, self.boxes
            for i in range(r.top // sh, (r.bottom - 1) // sh + 1):
                c = r.clip(0, i * sh, r.right, sh)
                boxes[i] = c if boxes[i] is None else boxes[i].union(c)
        return r

    def clear(self):
        """Deja la superficie transparente borrando sólo lo pintado en el fotograma que termina."""
        for r in self.boxes:
            if r is not None:
                self.surf.fill((0, 0, 0, 0), r)
        self.prev, self.boxes = self.boxes, [None] * self.STRIPS

    def take_dirty(self):
        """Rects nativos que la GPU debe volver a subir: lo pintado ahora y lo que había antes."""
        out = []
        for a, b in zip(self.boxes, self.prev):
            r = a if b is None else b if a is None else a.union(b)
            if r is not None:
                if out and out[-1].x == r.x and out[-1].w == r.w and out[-1].bottom == r.top:
                    out[-1] = out[-1].union(r)      # franjas contiguas iguales: una sola subida
                else:
                    out.append(r)
        self.prev = list(self.boxes)
        return out

    # --- pintar ---
    def fill(self, color, rect=None, special_flags=0):
        r = self.full if rect is None else _rect(rect, self.k)
        self.surf.fill(color, r, special_flags)
        if len(color) < 4 or color[3] or special_flags:
            self._mark(r)
        return r

    def blit(self, src, dest, area=None, special_flags=0):
        k = self.k
        x, y = dest[:2] if not isinstance(dest, pygame.Rect) else dest.topleft
        pos = (_r(x, k), _r(y, k))
        if isinstance(src, HiText):
            img = src.img if area is None else src.img.subsurface(_rect(area, src.k))
        elif k == 1:
            img = src if area is None else src.subsurface(area)
        else:
            # una superficie lógica (panel, recuadro): se lleva a escala; los retratos y textos ya llegan nativos
            if area is not None:
                src = src.subsurface(area)
            size = (_r(src.get_width(), k), _r(src.get_height(), k))
            try:
                img = pygame.transform.smoothscale(src, size)
            except ValueError:
                img = pygame.transform.scale(src, size)
            if src.get_alpha() is not None and not src.get_flags() & pygame.SRCALPHA:
                img.set_alpha(src.get_alpha())
        return self._mark(self.surf.blit(img, pos, None, special_flags))

    def blit_native(self, img, pos, special_flags=0):
        """Para quien ya ha compuesto a resolución nativa (retratos)."""
        return self._mark(self.surf.blit(img, pos, None, special_flags))


def native(surf, rect):
    """(superficie real, rect en ella, escala) para quien quiera pintar a resolución nativa."""
    k = getattr(surf, "k", 1)
    if isinstance(surf, UISurface):
        return surf.surf, _rect(rect, k), k
    return surf, pygame.Rect(rect), 1


def panel(surf, rect, rgba):
    """Rectángulo translúcido mezclado sobre lo que haya (velos, bandas, recuadros)."""
    target, r, _ = native(surf, rect)
    r = r.clip(target.get_rect())
    if not (r.w and r.h):
        return r
    if len(rgba) == 3 or rgba[3] >= 255:
        target.fill(rgba[:3], r)
    else:
        s = pygame.Surface(r.size, pygame.SRCALPHA)
        s.fill(rgba)
        target.blit(s, r.topleft)
    if isinstance(surf, UISurface):
        surf._mark(r)
    return r


class draw:
    """`pygame.draw` que entiende `UISurface`: coordenadas y grosores lógicos, rasterizado nativo."""

    @staticmethod
    def rect(surf, color, rect, width=0, **kw):
        if not isinstance(surf, UISurface):
            return pygame.draw.rect(surf, color, rect, width, **kw)
        k = surf.k
        kw = {key: _r(v, k) if v > 0 else v for key, v in kw.items()}
        return surf._mark(pygame.draw.rect(surf.surf, color, _rect(rect, k), _w(width, k), **kw))

    @staticmethod
    def circle(surf, color, center, radius, width=0):
        if not isinstance(surf, UISurface):
            return pygame.draw.circle(surf, color, center, radius, width)
        k = surf.k
        return surf._mark(pygame.draw.circle(surf.surf, color, (center[0] * k, center[1] * k), radius * k, _w(width, k)))

    @staticmethod
    def ellipse(surf, color, rect, width=0):
        if not isinstance(surf, UISurface):
            return pygame.draw.ellipse(surf, color, rect, width)
        return surf._mark(pygame.draw.ellipse(surf.surf, color, _rect(rect, surf.k), _w(width, surf.k)))

    @staticmethod
    def polygon(surf, color, points, width=0):
        if not isinstance(surf, UISurface):
            return pygame.draw.polygon(surf, color, points, width)
        k = surf.k
        return surf._mark(pygame.draw.polygon(surf.surf, color, [(x * k, y * k) for x, y in points], _w(width, k)))

    @staticmethod
    def line(surf, color, a, b, width=1):
        if not isinstance(surf, UISurface):
            return pygame.draw.line(surf, color, a, b, width)
        k = surf.k
        return surf._mark(pygame.draw.line(surf.surf, color, (a[0] * k, a[1] * k), (b[0] * k, b[1] * k), _w(width, k)))

    @staticmethod
    def lines(surf, color, closed, points, width=1):
        if not isinstance(surf, UISurface):
            return pygame.draw.lines(surf, color, closed, points, width)
        k = surf.k
        return surf._mark(pygame.draw.lines(surf.surf, color, closed, [(x * k, y * k) for x, y in points], _w(width, k)))
