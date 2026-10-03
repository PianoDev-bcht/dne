from types import SimpleNamespace

from django.test import SimpleTestCase

from assistant_dashboard.services.matching import classify_domain, domain_key, full_name, normalize_name

LYON = SimpleNamespace(name="Lyon")
POLY = SimpleNamespace(name="Polynesie")
CLERMONT = SimpleNamespace(name="Clermont-Ferrand")
BY_KEY = {"ac_lyon_fr": LYON, "ac_polynesie_pf": POLY}
BY_NAME = {"lyon": [LYON], "polynesie": [POLY], "clermont ferrand": [CLERMONT]}


class NormalizationTests(SimpleTestCase):
    def test_normalize_name(self):
        self.assertEqual(normalize_name("  Orléans-Tours "), "orleans tours")
        self.assertEqual(normalize_name("LA RÉUNION"), "la reunion")
        self.assertEqual(normalize_name("Polynésie française"), "polynesie")
        self.assertEqual(normalize_name("Clermont"), "clermont ferrand")

    def test_domain_key(self):
        self.assertEqual(domain_key("ac-nancy-metz.fr"), "ac_nancy_metz_fr")
        self.assertEqual(domain_key("@ac-noumea.nc"), "ac_noumea_nc")


class ClassifyDomainTests(SimpleTestCase):
    def test_exact_match(self):
        self.assertEqual(classify_domain("ac_lyon_fr", BY_KEY, BY_NAME)[:2], ("academie", LYON))

    def test_spelling_variant_via_label(self):
        category, loc, _ = classify_domain(
            "ac_clermont_fr", BY_KEY, BY_NAME, "Nombre d'utilisateurs de l'académie de Clermont")
        self.assertEqual((category, loc), ("academie", CLERMONT))

    def test_unknown_domain_not_matched(self):
        self.assertEqual(classify_domain("ac_atlantide_fr", BY_KEY, BY_NAME, "académie d'Atlantide")[:2],
                         ("non_apparie", None))

    def test_ambiguous_name_not_matched(self):
        by_name = {"lyon": [LYON, SimpleNamespace(name="Lyon bis")]}
        self.assertEqual(classify_domain("ac_x_fr", {}, by_name, "académie de Lyon")[:2], ("non_apparie", None))

    def test_non_territorial_domain_never_located(self):
        # même si un lieu portait ce nom, un domaine national reste hors carte
        self.assertEqual(classify_domain("pix_fr", {"pix_fr": LYON}, BY_NAME)[:2], ("national", None))

    def test_regional_domain_is_ambiguous(self):
        self.assertEqual(classify_domain("region_academique_paca_fr", BY_KEY, BY_NAME)[:2], ("regional", None))


class DisplayNameTests(SimpleTestCase):
    def test_elision_and_vice_rectorats(self):
        self.assertEqual(full_name("Lille"), "Académie de Lille")
        self.assertEqual(full_name("Amiens"), "Académie d'Amiens")
        self.assertEqual(full_name("Orléans-Tours"), "Académie d'Orléans-Tours")
        self.assertEqual(full_name("La Réunion"), "Académie de La Réunion")
        self.assertEqual(full_name("Noumea"), "Vice-rectorat de Nouvelle-Calédonie")
        self.assertEqual(full_name("Polynesie"), "Vice-rectorat de Polynésie française")
