from app.extensions import db

DELIVERY_NOTE_SEQUENCE = "delivery_note"


class DocumentSequence(db.Model):
    """Último número usado por tipo de documento.

    Se toma con bloqueo dentro de la misma transacción que crea el documento: si la
    transacción falla, el número vuelve atrás y la numeración no deja huecos.
    """

    __tablename__ = "document_sequences"

    name = db.Column(db.String(40), primary_key=True)
    last_value = db.Column(db.Integer, nullable=False, default=0)
