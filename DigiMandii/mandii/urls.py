from django.contrib import admin
from django.urls import path
from . import views
from django.conf.urls.static import static
from django.conf import settings
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('', views.index, name='index'),
    path('product/<int:id>', views.detail, name='detail'),
    
    # Navigation pages
    path('dashboard/', views.dashboard_view, name='dashboard'),
    path('sales/', views.sales_view, name='sales'),
    path('orders/', views.orders_view, name='orders'),
    path('profile/', views.profile_view, name='profile'),
    path('settings/', views.settings_view, name='settings'),
    
    # Product management
    path('create-product/', views.create_product_view, name='create_product'),
    path('edit-product/<int:id>/', views.edit_product_view, name='edit_product'),
    path('delete-product/<int:id>/', views.delete_product_view, name='delete_product'),
    
    # Payment related
    path('create-checkout-session/<int:id>/', views.create_checkout_session, name='create_checkout_session'),
    path('payment-verification/', views.payment_verification, name='payment_verification'),
    path('payment-success/<int:order_id>/', views.payment_success_view, name='payment_success'),
    path('payment-failed/', views.payment_failed_view, name='payment_failed'),
    path('api/checkout-session/<int:id>/', views.create_checkout_session, name='api_checkout_session'),
    # path('razorpay-webhook/', views.razorpay_webhook, name='razorpay_webhook'),
    
    # Wishlist
    path('wishlist/add/<int:product_id>/', views.add_to_wishlist, name='add_to_wishlist'),
    
    # Orders
    path('my-orders/', views.my_orders_view, name='my_orders'),
    
    # Authentication (if using Django's built-in auth)
    path('accounts/login/', auth_views.LoginView.as_view(template_name='mandii/login.html'), name='login'),
    path('accounts/logout/', views.logout_view, name='logout'),

]

urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)