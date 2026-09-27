from apps.core.text import uk_sort_key


def test_ukrainian_alphabetical_order():
    names = ["Індичка", "Яблука", "Апельсини", "Ґрунтовка", "Гречка", "Їжак", "Єнот", "Ехо", "Іній", "Ирис"]
    assert sorted(names, key=uk_sort_key) == [
        "Апельсини", "Гречка", "Ґрунтовка", "Ехо", "Єнот", "Ирис", "Індичка", "Іній", "Їжак", "Яблука"]


def test_case_and_apostrophe_do_not_matter():
    assert sorted(["в'ялене", "Вершки", "вада"], key=uk_sort_key) == ["вада", "Вершки", "в'ялене"]
