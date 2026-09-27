# seed/ — стартовые справочники

Извлечены из Excel-прототипа `menu_v3_nakaz_931.xlsx`. Грузятся командой `load_seed`.

| Файл | Записей | Формат |
|---|---|---|
| `diets.json` | 8 | `{code, name, kind: variant\|special, kcal:[min,max]\|null, protein_g, fat_g, carbs_g, indications}` |
| `products.json` | 72 | `{name, protein, fat, carbs, kcal}` — на 100 г нетто |
| `departments.json` | 15 | `{name, patient_category, default_diet}` |
| `dishes.json` | 130 | `{card_no, name, category, output_g, diets:[code], ingredients:[{product, brutto_g, netto_g}]}` |
| `menus.json` | 250 | `{number, diet, meals:{breakfast:[card_no], second_breakfast, lunch, afternoon_snack, dinner}}` |
| `substitutions.json` | 26 | `{basis: protein\|carbs, product, qty_g, substitute, substitute_qty_g}` |
| `seasonal_waste.json` | 13 | `{product, until_jan1, jan_feb, mar_apr, may_aug, sep_dec}` — строки как в документе |

Связи по естественным ключам: продукт — по `name`, страва — по `card_no`, дієта — по `code`.

Диапазоны меню по дієтам: ОВД 1–80, ЩД 81–135, ВБД 136–175, НБД 176–200, НКД 201–220, №9 221–250.
`patient_category` в `departments.json`: «Дорослі», «Діти», «Породіллі» → маппинг на choices.

⚠ Все данные — стартовый черновик: 234 из 250 меню ниже нижней границы ккал своей дієти.
Грузить с `verified=False`, дієтсестра проверяет и правит в системе.
