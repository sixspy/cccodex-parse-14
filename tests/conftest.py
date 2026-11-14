import pytest
from django.conf import settings

@pytest.fixture
def client():
    from django.test import Client
    return Client()
