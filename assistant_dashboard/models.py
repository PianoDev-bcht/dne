from django.db import models


class BetaLocation(models.Model):
    """Académie issue du dataset de la phase bêta (statique après import).

    Les effectifs bêta sont des *inscriptions à la bêta* : ils ne doivent jamais
    être présentés comme des utilisateurs de production.
    """

    name = models.CharField(max_length=100)
    normalized_name = models.CharField(max_length=100)
    email_domain = models.CharField(max_length=100)  # ex. "ac-nancy-metz.fr"
    domain_key = models.CharField(max_length=100, unique=True)  # ex. "ac_nancy_metz_fr"
    region_email = models.CharField(max_length=100, blank=True)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    beta_chefs_etablissement = models.PositiveIntegerField(default=0)
    beta_enseignants = models.PositiveIntegerField(default=0)
    beta_administration = models.PositiveIntegerField(default=0)
    beta_inspecteurs = models.PositiveIntegerField(default=0)
    beta_total = models.PositiveIntegerField(default=0)
    geo_shape = models.JSONField(null=True, blank=True)  # géométrie GeoJSON (contours 2020)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    @property
    def display_name(self):
        from .services.matching import display_name
        return display_name(self.name)

    @property
    def full_name(self):
        from .services.matching import full_name
        return full_name(self.name)


class ProductionObservation(models.Model):
    """Valeurs cumulées pour un domaine de messagerie à une date de snapshot."""

    CATEGORY_ACADEMIE = "academie"
    CATEGORY_REGIONAL = "regional"
    CATEGORY_NATIONAL = "national"
    CATEGORY_UNMATCHED = "non_apparie"
    CATEGORY_CHOICES = [
        (CATEGORY_ACADEMIE, "Académie"),
        (CATEGORY_REGIONAL, "Région académique (multi-académies)"),
        (CATEGORY_NATIONAL, "National / opérateur"),
        (CATEGORY_UNMATCHED, "Non apparié"),
    ]

    date = models.DateField(db_index=True)  # date Europe/Paris du snapshot
    domain = models.CharField(max_length=100, db_index=True)  # ex. "ac_lyon_fr"
    normalized_domain = models.CharField(max_length=100)
    label = models.CharField(max_length=200, blank=True)
    category = models.CharField(max_length=20, choices=CATEGORY_CHOICES)
    cumulative_users = models.PositiveIntegerField()
    cumulative_messages = models.PositiveIntegerField()
    location = models.ForeignKey(
        BetaLocation, null=True, blank=True, on_delete=models.SET_NULL, related_name="observations"
    )
    source_timestamp = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["date", "domain"]
        constraints = [
            models.UniqueConstraint(fields=["date", "domain"], name="unique_observation_date_domain")
        ]

    def __str__(self):
        return f"{self.date} {self.domain}"

    @classmethod
    def latest_date(cls):
        """Date du snapshot le plus récent en base (None si la base est vide)."""
        return cls.objects.aggregate(latest=models.Max("date"))["latest"]


class SchoolHoliday(models.Model):
    """Période de vacances scolaires d'une académie (dataset `fr-en-calendrier-scolaire`).

    Sert de contexte : une comparaison sur 14 jours qui recouvre des vacances
    propres à l'académie (non communes aux zones A, B et C) ne déclenche pas
    d'action (le signal reste « à suivre »).
    """

    location = models.ForeignKey(BetaLocation, on_delete=models.CASCADE, related_name="holidays")
    description = models.CharField(max_length=200)
    start = models.DateField()  # premier jour de vacances (heure de Paris)
    end = models.DateField()    # dernier jour de vacances
    school_year = models.CharField(max_length=9)  # ex. "2026-2027"
    zone = models.CharField(max_length=50, blank=True)  # ex. "Zone A", "Réunion"

    class Meta:
        ordering = ["location", "start"]
        constraints = [
            models.UniqueConstraint(fields=["location", "description", "start"], name="unique_holiday_period")
        ]

    def __str__(self):
        return f"{self.location} {self.description} {self.start}–{self.end}"


class ImportRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_SUCCESS = "success"
    STATUS_WARNING = "warning"
    STATUS_ERROR = "error"
    STATUS_UNCHANGED = "unchanged"  # source joignable, pas de nouveau snapshot : état normal
    STATUS_CHOICES = [
        (STATUS_RUNNING, "En cours"),
        (STATUS_SUCCESS, "Succès"),
        (STATUS_WARNING, "Succès avec anomalies"),
        (STATUS_ERROR, "Erreur"),
        (STATUS_UNCHANGED, "Pas de nouvelle publication"),
    ]
    COMPLETED = (STATUS_SUCCESS, STATUS_WARNING)  # imports complets : seuls à porter des anomalies

    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    number_of_records_fetched = models.PositiveIntegerField(default=0)
    number_of_records_created = models.PositiveIntegerField(default=0)
    number_of_records_updated = models.PositiveIntegerField(default=0)
    anomalies = models.JSONField(default=list, blank=True)  # [{type, domain, date, detail}]
    error_message = models.TextField(blank=True)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.started_at:%Y-%m-%d %H:%M} {self.status}"
