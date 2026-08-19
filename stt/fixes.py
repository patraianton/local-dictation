# -*- coding: utf-8 -*-
"""Словарь замен: «что услышала» -> «как правильно».

Файл fixes.tsv, четыре колонки через табуляцию:
    услышала <TAB> правильно <TAB> сколько раз пригодилось <TAB> auto|manual
Пополняется сам (см. learn.py) и руками — можно просто дописать строку.
"""
import re
import threading
from pathlib import Path


# Замена начинается с русской строчной буквы — значит это обычное слово,
# а не название со своим написанием.
CYRILLIC_LOWER_RE = re.compile(r"[а-яё]", re.UNICODE)


def yo_key(word: str) -> str:
    """«ё» и «е» — это одно и то же написание, а не разные слова."""
    return word.lower().replace("ё", "е")


class Fixes:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self.pairs: dict[str, tuple[str, int, str]] = {}
        self._regex = None
        self.load()

    def load(self) -> None:
        pairs: dict[str, tuple[str, int, str]] = {}
        if self.path.exists():
            for line in self.path.read_text(encoding="utf-8").splitlines():
                if not line.strip() or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2:
                    continue
                src, dst = parts[0].strip(), parts[1].strip()
                hits = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
                origin = parts[3].strip() if len(parts) > 3 else "manual"
                if src and dst:
                    pairs[src.lower()] = (dst, hits, origin)
        with self._lock:
            self.pairs = pairs
            self._rebuild()

    def _rebuild(self) -> None:
        if not self.pairs:
            self._regex = None
            return
        # Длинные сначала, иначе короткое совпадение съест длинное.
        keys = sorted(self.pairs, key=len, reverse=True)
        body = "|".join(re.escape(k) for k in keys)
        self._regex = re.compile(rf"(?<!\w)({body})(?!\w)", re.IGNORECASE | re.UNICODE)

    def apply(self, text: str) -> tuple[str, int]:
        with self._lock:
            regex, pairs = self._regex, dict(self.pairs)
        if not regex or not text:
            return text, 0
        count = 0

        def sub(m):
            nonlocal count
            count += 1
            said = m.group(1)
            dst = pairs[said.lower()][0]
            # Регистр берём у услышанного. В словаре замены записаны маленькими
            # буквами, а распознавалка ставит заглавную в начале предложения:
            # без этого «Все, закончили.» превращалось в «все, закончили.».
            #
            # Только для русских слов. У названий написание своё и оно главнее:
            # «herdr» и «worktree» пишутся маленькими всегда, а «iPhone» — так,
            # как записано. Заглавную в начале предложения им поставит корректор,
            # он видит, где предложение начинается.
            if said[:1].isupper() and CYRILLIC_LOWER_RE.match(dst):
                dst = dst[:1].upper() + dst[1:]
            return dst

        return regex.sub(sub, text), count

    def add(self, src: str, dst: str, origin: str = "auto") -> bool:
        """Добавляет пару. Возвращает True, если пара новая.

        Две пары сюда не пускаем совсем — обе однажды испортили текст:
        - разница только в «е/ё», добавляет машина. «все» и «всё» — разные
          слова, вслепую их менять нельзя: вышло «на всё статьи», «всё
          картинки», «всё звонки». По смыслу решает корректор, он видит
          соседние слова. Руками такую пару добавить можно — человек видит смысл.
        - встречная пара. Уже случалось: «все -> всё» И «всё -> все» лежали
          рядом и воевали, портя текст в обе стороны.
        """
        src, dst = src.strip(), dst.strip()
        if not src or not dst or src.lower() == dst.lower():
            return False
        key = src.lower()
        if origin == "auto" and yo_key(key) == yo_key(dst):
            return False
        with self._lock:
            back = self.pairs.get(dst.lower())
            if back and back[0].lower() == key:
                return False
            if key in self.pairs:
                old_dst, hits, old_origin = self.pairs[key]
                self.pairs[key] = (dst, hits + 1, old_origin)
                new = False
            else:
                self.pairs[key] = (dst, 1, origin)
                new = True
            self._rebuild()
            self._save_locked()
        return new

    def _save_locked(self) -> None:
        lines = [
            "# услышала\tправильно\tсколько раз\tоткуда",
            "# Можно дописывать руками. Перезапуск не нужен только для ручной правки через окно.",
        ]
        for key in sorted(self.pairs):
            dst, hits, origin = self.pairs[key]
            lines.append(f"{key}\t{dst}\t{hits}\t{origin}")
        self.path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def __len__(self) -> int:
        return len(self.pairs)
