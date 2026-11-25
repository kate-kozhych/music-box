from django.urls import path

from musicbox_core.apps.diary.api.views import DiaryEntriesView, DiaryEntryView, UserDiaryView

urlpatterns = [
    path("diary-entries", DiaryEntriesView.as_view(), name="diary-entries"),
    path("diary-entries/<str:entry_id>", DiaryEntryView.as_view(), name="diary-entry"),
    path("users/<str:username>/diary", UserDiaryView.as_view(), name="user-diary"),
]
