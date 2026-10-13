"""Встроенная база классов DayZ: краткие описания, иерархия скриптов и ванильные конфиги.

DESCRIPTIONS — описания базовых классов (для чего используется класс как родитель).
SCRIPT_PARENTS — иерархия скриптовых классов Enforce Script (без исходников игры).
Ванильные классы из config.cpp лежат в vanilla_classes.tsv.gz (tools/build_classdb.py).
"""

from __future__ import annotations

import gzip
import os
from typing import Optional

# ---------------------------------------------------------------------------
# Иерархия скриптов (то, что есть в scripts.pbo, но не в config.cpp)
# ---------------------------------------------------------------------------

SCRIPT_PARENTS = {
    "Managed": "Class",
    "IEntity": "Managed",
    "Object": "IEntity",
    "ObjectTyped": "Object",
    "Entity": "ObjectTyped",
    "EntityAI": "Entity",
    "InventoryItem": "EntityAI",
    "ItemBase": "InventoryItem",
    "Inventory_Base": "ItemBase",
    "Clothing_Base": "ItemBase",
    "Clothing": "Clothing_Base",
    "Edible_Base": "ItemBase",
    "Bottle_Base": "Edible_Base",
    "Container_Base": "ItemBase",
    "DeployableContainer_Base": "Container_Base",
    "BaseBuildingBase": "ItemBase",
    "Fence": "BaseBuildingBase",
    "Watchtower": "BaseBuildingBase",
    "TentBase": "ItemBase",
    "FireplaceBase": "ItemBase",
    "Fireplace": "FireplaceBase",
    "TrapBase": "ItemBase",
    "Weapon": "InventoryItem",
    "Weapon_Base": "Weapon",
    "Rifle_Base": "Weapon_Base",
    "Pistol_Base": "Weapon_Base",
    "BoltActionRifle_Base": "Rifle_Base",
    "Magazine": "InventoryItem",
    "Magazine_Base": "Magazine",
    "Ammunition_Base": "Magazine_Base",
    "Man": "EntityAI",
    "Human": "Man",
    "DayZPlayer": "Human",
    "DayZPlayerImplement": "DayZPlayer",
    "ManBase": "DayZPlayerImplement",
    "PlayerBase": "ManBase",
    "SurvivorBase": "PlayerBase",
    "DayZCreature": "EntityAI",
    "DayZCreatureAI": "DayZCreature",
    "DayZInfected": "DayZCreatureAI",
    "ZombieBase": "DayZInfected",
    "ZombieMaleBase": "ZombieBase",
    "ZombieFemaleBase": "ZombieBase",
    "DayZAnimal": "DayZCreatureAI",
    "AnimalBase": "DayZAnimal",
    "Transport": "EntityAI",
    "Car": "Transport",
    "CarScript": "Car",
    "Boat": "Transport",
    "BoatScript": "Boat",
    "Building": "EntityAI",
    "BuildingBase": "Building",
    "MissionBase": "MissionBaseWorld",
    "MissionBaseWorld": "Mission",
    "MissionServer": "MissionBase",
    "MissionGameplay": "MissionBase",
    "MissionMainMenu": "MissionBase",
    "DayZGame": "CGame",
    "ActionBase": "ActionBase_Basic",
    "AnimatedActionBase": "ActionBase",
    "ActionSingleUseBase": "AnimatedActionBase",
    "ActionContinuousBase": "AnimatedActionBase",
    "ActionInteractBase": "AnimatedActionBase",
    "ActionSequentialBase": "AnimatedActionBase",
    "ActionInstantBase": "ActionBase",
    "UIScriptedMenu": "UIMenuPanel",
    "ScriptedWidgetEventHandler": "Managed",
    "Timer": "TimerBase",
    "TimerBase": "Managed",
    "EffectSound": "Effect",
    "EffectParticle": "Effect",
}

# ---------------------------------------------------------------------------
# Описания
# ---------------------------------------------------------------------------

DESCRIPTIONS = {
    # --- базовые скриптовые классы
    "Class": "Корень всех классов Enforce Script.",
    "Managed": "Базовый класс с автоматическим управлением памятью (подсчёт ссылок). "
               "От него наследуют обычные скриптовые классы-помощники, менеджеры, данные.",
    "IEntity": "Базовая сущность движка Enfusion, всё, что существует в мире.",
    "Object": "Любой объект мира: модель, позиция, ориентация, повреждения.",
    "ObjectTyped": "Объект с типом из конфига (CfgVehicles и т.п.).",
    "Entity": "Объект мира с типом из конфига, может участвовать в симуляции.",
    "EntityAI": "Объект с инвентарём, здоровьем, вложениями и событиями (EEInit, EEKilled…). "
                "Общий предок предметов, игроков, зомби, животных и машин.",
    "InventoryItem": "Нативный базовый класс предмета инвентаря.",
    "ItemBase": "Главный скриптовый класс всех предметов: количество, жидкости, температура, "
                "ремонт, действия (SetActions), сохранение. Наследуйте свои предметы от него.",
    "Inventory_Base": "Базовый класс обычных предметов инвентаря (в конфиге CfgVehicles и в скриптах). "
                      "Типовой родитель для своего предмета.",
    "Static": "Корневой конфиг-класс статических объектов CfgVehicles (предметы, здания).",
    "All": "Корень всей иерархии CfgVehicles.",
    "AllVehicles": "Конфиг-предок всех «живых» и управляемых объектов (люди, техника).",

    # --- игрок и персонажи
    "Man": "Базовый класс человекоподобных персонажей (игрок, AI).",
    "Human": "Нативный класс человека: анимации, команды движения, инвентарь рук.",
    "DayZPlayer": "Нативный класс игрока DayZ: камера, команды, состояние.",
    "DayZPlayerImplement": "Скриптовая реализация игрока: обработка ввода, анимационные события, "
                           "камера, падения, оружие.",
    "ManBase": "Промежуточный класс между реализацией игрока и PlayerBase.",
    "PlayerBase": "Главный класс игрока: статы (голод, жажда, кровь), модификаторы, болезни, "
                  "действия, RPC, сохранение. Чаще всего модифицируется через «modded class».",
    "SurvivorBase": "Конфиг-предок всех моделей выживших (SurvivorM_*/SurvivorF_*).",
    "SurvivorMale_Base": "Базовый конфиг мужского персонажа-выжившего.",
    "SurvivorFemale_Base": "Базовый конфиг женского персонажа-выжившего.",
    "Man_Base": "Конфиг-предок людей, зомби и животных с упрощённым AI.",
    "DZ_LightAI": "Конфиг-предок существ с лёгким AI (зомби, животные).",

    # --- зомби и животные
    "DayZCreature": "Нативный класс существа (зомби, животные).",
    "DayZCreatureAI": "Существо, управляемое AI.",
    "DayZInfected": "Нативный класс заражённого (зомби).",
    "ZombieBase": "Скриптовый класс заражённых: атаки, реакция на шум, урон, анимации.",
    "ZombieMaleBase": "Базовый класс мужских заражённых (ZmbM_*).",
    "ZombieFemaleBase": "Базовый класс женских заражённых (ZmbF_*).",
    "LowTierMaleZombieBase": "Мужские заражённые низкого уровня (меньше здоровья).",
    "LowTierFemaleZombieBase": "Женские заражённые низкого уровня.",
    "DayZAnimal": "Нативный класс животного.",
    "AnimalBase": "Базовый класс животных: волки, олени, коровы, медведи и т.д.",

    # --- одежда
    "Clothing_Base": "Скриптовая основа одежды: утепление, промокание, вложения-слоты.",
    "Clothing": "Базовый класс одежды и снаряжения, надеваемого в слоты игрока "
                "(куртки, штаны, рюкзаки, жилеты, головные уборы).",
    "Head": "Голова персонажа (модель и текстуры лица).",
    "Head_Default": "Стандартная голова персонажа.",
    "MaleTorso_Base": "Голый торс мужского персонажа (то, что видно без одежды).",
    "MaleLegs_Base": "Голые ноги мужского персонажа.",
    "MaleFeet_Base": "Голые ступни мужского персонажа.",
    "MaleHands_Base": "Голые руки мужского персонажа.",
    "FemaleTorso_Base": "Голый торс женского персонажа.",
    "FemaleLegs_Base": "Голые ноги женского персонажа.",
    "FemaleFeet_Base": "Голые ступни женского персонажа.",
    "FemaleHands_Base": "Голые руки женского персонажа.",
    "Armband_ColorBase": "Повязка на руку (цветные варианты, флаги).",
    "PlateCarrierVest": "Бронежилет Plate Carrier с вложениями (подсумки, кобура).",

    # --- еда, напитки, медицина
    "Edible_Base": "Всё, что можно съесть или выпить: еда, напитки, лекарства; стадии готовки, "
                   "питательность, порча.",
    "Bottle_Base": "Ёмкости для жидкостей (бутылки, фляги, кастрюли): наполнение, кипячение, разлив.",
    "MushroomBase": "Грибы (съедобные и ядовитые).",
    "SodaCan_ColorBase": "Банки газировки.",
    "Pelt_Base": "Шкуры животных (выделка, крафт).",
    "PlantBase": "Растения на грядке (рост, полив, урожай).",
    "SeedBase": "Семена для посадки.",
    "BandageDressing": "Бинт (остановка кровотечения).",
    "Morphine": "Морфин (обезболивание, лечение переломов).",
    "Epinephrine": "Адреналин (шприц-инъектор).",
    "BloodBagIV": "Пакет крови с системой (переливание).",
    "SalineBag": "Физраствор.",

    # --- оружие
    "Weapon": "Нативный базовый класс оружия.",
    "Weapon_Base": "Скриптовая основа огнестрельного оружия: стрельба, перезарядка, "
                   "магазины, обвесы, состояние затвора.",
    "Rifle_Base": "Базовый класс винтовок и автоматов.",
    "Pistol_Base": "Базовый класс пистолетов и револьверов.",
    "BoltActionRifle_Base": "Болтовые винтовки (ручная перезарядка затвора).",
    "BoltActionRifle_ExternalMagazine_Base": "Болтовые винтовки с отъёмным магазином.",
    "BoltActionRifle_InnerMagazine_Base": "Болтовые винтовки со встроенным магазином.",
    "RifleBoltFree_Base": "Автоматическое оружие со свободным затвором.",
    "RifleBoltLock_Base": "Автоматическое оружие с запираемым затвором.",
    "DoubleBarrel_Base": "Двуствольные ружья.",
    "Launcher_Base": "Гранатомёты и ракетницы.",
    "Archery_Base": "Луки и арбалеты.",
    "Crossbow_Base": "Арбалеты.",
    "Magazine": "Нативный класс магазина.",
    "Magazine_Base": "Магазины и обоймы для оружия (CfgMagazines).",
    "Ammunition_Base": "Патроны россыпью (пачки патронов в инвентаре).",
    "Bullet_Base": "Базовый боеприпас-пуля (CfgAmmo): урон, баллистика.",
    "DefaultAmmo": "Корень классов боеприпасов CfgAmmo.",
    "MeleeDamage": "Параметры урона холодным оружием (CfgAmmo).",
    "MeleeZombie": "Урон от атак заражённых (CfgAmmo).",
    "ItemOptics": "Оптические прицелы и бинокли.",
    "ItemOptics_Base": "Базовый класс оптики на оружие.",
    "ItemSuppressor": "Глушители.",
    "FxCartridge": "Визуальная гильза/патрон, выбрасываемая при стрельбе.",
    "FxRound": "Визуальный патрон (эффект), не предмет инвентаря.",

    # --- взрывчатка и ловушки
    "ExplosivesBase": "Взрывчатка: гранаты, мины, СВУ — взрыв, урон по области.",
    "Grenade_Base": "Гранаты (чека, таймер, бросок).",
    "Trap_Base": "Ловушки (медвежий капкан, мина, силки).",
    "TrapBase": "Скриптовая основа ловушек.",

    # --- контейнеры, базы
    "Container_Base": "Контейнеры с собственным грузом: ящики, бочки, палатки, сундуки.",
    "DeployableContainer_Base": "Размещаемые на земле контейнеры.",
    "Box_Base": "Коробки (патроны, еда), которые можно распаковать.",
    "TentBase": "Палатки: установка, сворачивание, грузовой отсек.",
    "BaseBuildingBase": "Строительство базы: стены, вышки, ворота — этапы постройки и разрушение.",
    "Fence": "Забор/ворота базы (этапы, кодовый замок).",
    "Watchtower": "Сторожевая вышка базы.",
    "FireplaceBase": "Костры, печи: топливо, огонь, готовка.",
    "Flag_Base": "Флаги для флагштока базы.",
    "Powered_Base": "Предметы с электропитанием (батареи, генераторы).",
    "Switchable_Base": "Включаемые предметы: фонари, налобники, рации.",
    "Chemlight_ColorBase": "Химический источник света.",
    "Book_Base": "Книги.",
    "ItemBook": "Книга, которую можно прочитать.",

    # --- техника
    "Transport": "Нативный класс транспорта (места, экипаж).",
    "Car": "Нативный класс машины: двигатель, колёса, физика.",
    "CarScript": "Скриптовый класс машин: двери, топливо, радиатор, свет, звуки. "
                 "Родитель для своих автомобилей.",
    "Boat": "Нативный класс лодки.",
    "BoatScript": "Скриптовый класс лодок.",
    "CarDoor": "Двери, капот и багажник машины (как вложения).",
    "CarWheel": "Колёса машины.",

    # --- здания и мир
    "Building": "Здания и сооружения.",
    "BuildingBase": "Скриптовая основа зданий (двери, окна).",
    "House": "Здание/статический объект карты (CfgVehicles).",
    "HouseNoDestruct": "Неразрушаемый объект карты: дома, руины, обломки, декор.",
    "ProxyAttachment": "Прокси-модель для отображения вложения на предмете.",
    "TreeHard": "Твёрдые деревья (не ломаются при наезде).",
    "TreeSoft": "Мягкие деревья (ломаются).",
    "BushHard": "Твёрдые кусты.",
    "BushSoft": "Мягкие кусты.",
    "StaticObject": "Статический объект без симуляции (CfgNonAIVehicles).",

    # --- миссии и игра
    "CGame": "Нативный класс игры.",
    "DayZGame": "Глобальный объект игры (GetGame()): RPC, мир, время, загрузка.",
    "Mission": "Нативный класс миссии.",
    "MissionBaseWorld": "Промежуточный класс миссии.",
    "MissionBase": "Общая основа серверной и клиентской миссии.",
    "MissionServer": "Серверная миссия: подключение игроков, спавн, OnInit сервера. "
                     "Обычно расширяется через «modded class MissionServer».",
    "MissionGameplay": "Клиентская миссия: HUD, ввод, меню, чат.",
    "MissionMainMenu": "Миссия главного меню.",

    # --- действия, рецепты, плагины
    "ActionBase_Basic": "Самая базовая основа действий игрока.",
    "ActionBase": "Основа действий игрока (условия, анимации, текст подсказки).",
    "AnimatedActionBase": "Действие с анимацией.",
    "ActionSingleUseBase": "Мгновенное одноразовое действие (съесть таблетку, включить фонарь).",
    "ActionContinuousBase": "Действие с удержанием кнопки и прогрессом (разделка, ремонт, питьё).",
    "ActionInteractBase": "Взаимодействие с объектом мира (открыть дверь, сесть в машину).",
    "ActionSequentialBase": "Последовательное многоэтапное действие.",
    "ActionInstantBase": "Мгновенное действие без анимации.",
    "RecipeBase": "Рецепт крафта: ингредиенты, результат, условия.",
    "PluginBase": "Плагин (синглтон-модуль) с жизненным циклом OnInit/OnUpdate.",
    "ModifierBase": "Модификатор состояния игрока (голод, болезни, кровопотеря).",
    "SymptomBase": "Симптом болезни (кашель, чихание, рвота).",

    # --- интерфейс и утилиты
    "UIScriptedMenu": "Скриптовое меню/окно интерфейса (Init, OnShow, OnClick).",
    "UIMenuPanel": "Нативная панель меню.",
    "ScriptedWidgetEventHandler": "Обработчик событий виджетов (клики, наведение).",
    "TimerBase": "Основа таймеров.",
    "Timer": "Таймер с вызовом функции через интервал.",
    "Effect": "Визуальный/звуковой эффект.",
    "EffectSound": "Звуковой эффект.",
    "EffectParticle": "Эффект частиц.",

    # --- конфиг-секции
    "CfgPatches": "Объявление аддона: units, weapons, requiredAddons (порядок загрузки).",
    "CfgMods": "Описание мода и подключение его скриптовых модулей (defs).",
    "CfgVehicles": "Конфиг всех объектов мира и предметов инвентаря.",
    "CfgWeapons": "Конфиг оружия.",
    "CfgMagazines": "Конфиг магазинов и патронов в инвентаре.",
    "CfgAmmo": "Конфиг боеприпасов (урон, баллистика).",
    "CfgSoundSets": "Наборы звуков (soundSet), ссылаются на CfgSoundShaders.",
    "CfgSoundShaders": "Звуковые шейдеры: файлы звука, громкость, дальность.",
    "CfgSounds": "Простые звуки (playSound).",
    "default": "Стандартный шаблон звука CfgSounds.",
    "DZ_SurfacesExt": "Шаблон внешних поверхностей (CfgSurfaces).",
    "DZ_SurfacesInt": "Шаблон внутренних поверхностей (CfgSurfaces).",
}

# Описание по секции конфига, если для класса и предков ничего не нашлось
SECTION_DESCRIPTIONS = {
    "cfgpatches": "Объявление аддона (CfgPatches): requiredAddons задаёт порядок загрузки.",
    "cfgmods": "Описание мода и его скриптовых модулей (CfgMods).",
    "cfgvehicles": "Объект мира или предмет (CfgVehicles).",
    "cfgweapons": "Оружие (CfgWeapons).",
    "cfgmagazines": "Магазин или патроны (CfgMagazines).",
    "cfgammo": "Боеприпас (CfgAmmo).",
    "cfgsoundsets": "Набор звуков (CfgSoundSets).",
    "cfgsoundshaders": "Звуковой шейдер (CfgSoundShaders).",
    "cfgsounds": "Звук (CfgSounds).",
    "cfgnonaivehicles": "Статический объект/прокси (CfgNonAIVehicles).",
    "cfgslots": "Слот для вложений (CfgSlots).",
    "cfgsurfaces": "Поверхность земли (CfgSurfaces).",
    "cfgcloudlets": "Частицы (CfgCloudlets).",
    "cfgworlds": "Карта/мир (CfgWorlds).",
}

# Категории по папке ванильного конфига
FOLDER_CATEGORIES = {
    "gear\\books": "книги", "gear\\camping": "кемпинг", "gear\\consumables": "расходники",
    "gear\\containers": "контейнеры", "gear\\cooking": "готовка", "gear\\crafting": "крафт",
    "gear\\cultivation": "земледелие", "gear\\drinks": "напитки", "gear\\food": "еда",
    "gear\\medical": "медицина", "gear\\navigation": "навигация", "gear\\optics": "оптика",
    "gear\\radio": "рации", "gear\\tools": "инструменты", "gear\\traps": "ловушки",
    "characters\\backpacks": "рюкзаки", "characters\\belts": "ремни", "characters\\glasses": "очки",
    "characters\\gloves": "перчатки", "characters\\headgear": "головные уборы",
    "characters\\heads": "головы персонажей", "characters\\masks": "маски",
    "characters\\pants": "штаны", "characters\\shoes": "обувь", "characters\\tops": "верхняя одежда",
    "characters\\vests": "жилеты", "characters\\zombies": "заражённые", "animals": "животные",
    "weapons\\ammunition": "патроны", "weapons\\attachments": "обвесы оружия",
    "weapons\\explosives": "взрывчатка", "weapons\\firearms": "огнестрельное оружие",
    "weapons\\launchers": "гранатомёты", "weapons\\melee": "холодное оружие",
    "weapons\\pistols": "пистолеты", "weapons\\shotguns": "дробовики", "weapons\\archery": "луки и арбалеты",
    "weapons\\nonlethal": "нелетальное оружие", "weapons\\projectiles": "снаряды",
    "vehicles\\wheeled": "машины", "vehicles\\parts": "запчасти машин", "vehicles\\water": "лодки",
    "structures": "постройки карты", "plants": "растительность", "rocks": "камни",
    "sounds": "звуки", "surfaces": "поверхности", "worlds": "карты",
}


def folder_category(path: str) -> Optional[str]:
    p = path.lower().replace("/", "\\")
    if p.startswith("dz\\"):
        p = p[3:]
    p = p.replace("_bliss", "")
    best = None
    for k, v in FOLDER_CATEGORIES.items():
        if p.startswith(k) and (best is None or len(k) > len(best[0])):
            best = (k, v)
    return best[1] if best else None


# ---------------------------------------------------------------------------
# Ванильные конфиги
# ---------------------------------------------------------------------------

_VANILLA: Optional[dict] = None
SECTION_PRIORITY = ["CfgVehicles", "CfgWeapons", "CfgMagazines", "CfgAmmo", "CfgNonAIVehicles"]


def vanilla_path() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "vanilla_classes.tsv.gz")


def vanilla() -> dict:
    """имя (lower) -> список (имя, родитель, секция, файл)."""
    global _VANILLA
    if _VANILLA is None:
        data: dict = {}
        try:
            with gzip.open(vanilla_path(), "rt", encoding="utf-8") as fh:
                for line in fh:
                    parts = line.rstrip("\n").split("\t")
                    if len(parts) == 4:
                        data.setdefault(parts[0].lower(), []).append(tuple(parts))
        except OSError:
            pass
        for rows in data.values():
            rows.sort(key=lambda r: SECTION_PRIORITY.index(r[2]) if r[2] in SECTION_PRIORITY else 99)
        _VANILLA = data
    return _VANILLA


_DESC_LOWER = {k.lower(): v for k, v in DESCRIPTIONS.items()}
_SCRIPT_LOWER = {k.lower(): v for k, v in SCRIPT_PARENTS.items()}


def description(name: str) -> Optional[str]:
    return _DESC_LOWER.get(name.lower())


def script_parent(name: str) -> Optional[str]:
    return _SCRIPT_LOWER.get(name.lower())


def builtin_names() -> list[str]:
    names = {n.lower(): n for n in DESCRIPTIONS}
    for k, v in SCRIPT_PARENTS.items():
        names.setdefault(k.lower(), k)
        names.setdefault(v.lower(), v)
    return list(names.values())
