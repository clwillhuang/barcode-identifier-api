from django.urls import path
from django.urls.conf import re_path
from barcode_blastn import views

urlpatterns = [
    path('blastdbs/', views.BlastDbList.as_view()),
    path('blastdbs/<int:pk>/', views.BlastDbDetail.as_view()),
    path('nuccores/', views.NuccoreSequenceList.as_view()),
    path('nuccores/<int:pk>/', views.NuccoreSequenceDetail.as_view()),
    path('runs/', views.BlastRunList.as_view()),
    path('runs/<int:pk>/', views.BlastRunDetail.as_view()),
    re_path(r'^upload/(?P<filename>[^/]+)$', views.NuccoreSequenceListUpload.as_view())
]