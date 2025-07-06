from django.shortcuts import render, get_object_or_404, redirect
from django.contrib import messages
from .models import Product, OrderDetail, Wishlist
from django.conf import settings
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.admin.views.decorators import staff_member_required
import razorpay
from django.http import JsonResponse
import json
import logging
from .forms import ProductForm
from django.contrib.auth.decorators import login_required
from django.db import models
from django.contrib.auth import logout, login, authenticate
from django.contrib.auth.models import User
from django.contrib.auth.forms import UserCreationForm
from django.core.mail import send_mail
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from django.template.loader import render_to_string
from django.contrib.sites.shortcuts import get_current_site
from django.utils.encoding import force_str


# Set up logging
logger = logging.getLogger(__name__)

def index(request):
    products = Product.objects.filter(is_active=True)
    return render(request, 'mandii/index.html', {'products': products})

def detail(request, id):
    product = get_object_or_404(Product, id=id, is_active=True)
    razorpay_key_id = settings.RAZORPAY_KEY_ID
    return render(request, 'mandii/detail.html', {
        'product': product, 
        'razorpay_key_id': razorpay_key_id
    })

@csrf_exempt
@require_POST
def create_checkout_session(request, id):
    try:
        # Ensure user is logged in
        if not request.user.is_authenticated:
            return JsonResponse({'error': 'You must be logged in to place an order.'}, status=401)

        product = get_object_or_404(Product, id=id, is_active=True)

        # Parse data from frontend
        try:
            data = json.loads(request.body)
            quantity = int(data.get("quantity", 1))
            items_total = float(data.get("items_total", product.price))
            shipping_cost = float(data.get("shipping_cost", 0))
            total_amount = float(data.get("total_amount", product.price))
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({'error': 'Invalid request data'}, status=400)

        # Get email from authenticated user
        customer_email = request.user.email

        if not customer_email or '@' not in customer_email:
            return JsonResponse({'error': 'Your account email is invalid.'}, status=400)

        # Razorpay client setup
        try:
            client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        except Exception as e:
            logger.error(f"Razorpay client error: {str(e)}")
            return JsonResponse({'error': 'Payment service unavailable'}, status=500)

        # Razorpay order data
        amount = int(total_amount * 100)  # in paise
        order_data = {
            "amount": amount,
            "currency": "INR",
            "receipt": f"order_rcptid_{id}_{customer_email}",
            "payment_capture": 1
        }

        try:
            order = client.order.create(data=order_data)
        except razorpay.errors.RazorpayError as e:
            logger.error(f"Razorpay order creation failed: {str(e)}")
            return JsonResponse({'error': 'Failed to create payment order'}, status=500)

        # Save to database
        try:
            order_detail = OrderDetail.objects.create(
                user=request.user,
                customer_email=customer_email,
                product=product,
                quantity=quantity,
                amount=amount,
                shipping_cost=shipping_cost,
                total_amount=total_amount,
                razorpay_order_id=order['id'],
                payment_status='pending'
            )
            logger.info(f"Order created: {order_detail.id} for {customer_email}")
        except Exception as e:
            logger.error(f"Database save failed: {str(e)}")
            return JsonResponse({'error': 'Failed to save order details'}, status=500)

        return JsonResponse({
            "order_id": order['id'],
            "razorpay_key_id": settings.RAZORPAY_KEY_ID,
            "amount": amount,
            "currency": "INR",
            "product_name": product.name,
            "customer_email": customer_email,
            "order_id_db": order_detail.id
        })

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}")
        return JsonResponse({'error': 'An unexpected error occurred'}, status=500)

@login_required
@csrf_exempt
@require_POST
def payment_verification(request):
    """Verify payment after successful payment"""
    try:
        data = json.loads(request.body)
        razorpay_order_id = data.get('razorpay_order_id')
        razorpay_payment_id = data.get('razorpay_payment_id')
        razorpay_signature = data.get('razorpay_signature')
        
        if not all([razorpay_order_id, razorpay_payment_id, razorpay_signature]):
            return JsonResponse({'error': 'Missing required payment data'}, status=400)
        
        # Get order from database
        try:
            order = OrderDetail.objects.get(razorpay_order_id=razorpay_order_id)
        except OrderDetail.DoesNotExist:
            return JsonResponse({'error': 'Order not found'}, status=404)
        
        # Verify payment signature
        client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
        
        try:
            client.utility.verify_payment_signature({
                'razorpay_order_id': razorpay_order_id,
                'razorpay_payment_id': razorpay_payment_id,
                'razorpay_signature': razorpay_signature
            })
        except razorpay.errors.SignatureVerificationError:
            logger.error(f"Payment signature verification failed for order {razorpay_order_id}")
            return JsonResponse({'error': 'Payment verification failed'}, status=400)
        
        # Update order status
        order.razorpay_payment_id = razorpay_payment_id
        order.razorpay_signature = razorpay_signature
        order.has_paid = True
        order.payment_status = 'completed'
        order.save()
        
        logger.info(f"Payment verified successfully for order {order.id}")
        return JsonResponse({
            'status': 'success',
            'order_id': order.id,
            'redirect_url': f'/payment-success/{order.id}/'
        })
        
    except json.JSONDecodeError:
        return JsonResponse({'error': 'Invalid JSON data'}, status=400)
    except Exception as e:
        logger.error(f"Payment verification error: {str(e)}")
        return JsonResponse({'error': 'Payment verification failed'}, status=500)

@login_required
def payment_success_view(request, order_id):
    """Display payment success page"""
    try:
        order = get_object_or_404(OrderDetail, id=order_id, has_paid=True)
        
        context = {
            'order': order,
            'product': order.product,
            'amount_paid': order.amount_in_rupees,
            'order_id': order.id,
            'transaction_id': order.razorpay_payment_id,
            'customer_email': order.customer_email,
        }
        
        return render(request, 'mandii/payment_success.html', context)
        
    except Exception as e:
        logger.error(f"Error in payment_success_view: {str(e)}")
        messages.error(request, 'Order not found or payment not completed.')
        return redirect('index')

@login_required
def payment_failed_view(request):
    """Display payment failure page"""
    # Get failure details from session or query parameters
    razorpay_order_id = request.GET.get('order_id')
    error_code = request.GET.get('error_code')
    error_description = request.GET.get('error_description')
    
    order = None
    if razorpay_order_id:
        try:
            order = OrderDetail.objects.get(razorpay_order_id=razorpay_order_id)
            # Update order status to failed
            order.payment_status = 'failed'
            order.failure_reason = f"Error: {error_code} - {error_description}"
            order.save()
            
            logger.info(f"Payment failed for order {order.id}: {error_description}")
        except OrderDetail.DoesNotExist:
            logger.error(f"Order not found for failed payment: {razorpay_order_id}")
    
    context = {
        'order': order,
        'error_code': error_code,
        'error_description': error_description or 'Payment was cancelled or failed',
        'razorpay_order_id': razorpay_order_id,
    }
    
    return render(request, 'mandii/payment_failed.html', context)

@login_required
@csrf_exempt
def razorpay_webhook(request):
    """Handle Razorpay webhooks for payment events"""
    if request.method == 'POST':
        try:
            webhook_signature = request.META.get('HTTP_X_RAZORPAY_SIGNATURE')
            webhook_body = request.body
            
            if not webhook_signature:
                return JsonResponse({'error': 'Missing webhook signature'}, status=400)
            
            # Verify webhook signature (if webhook secret is configured)
            if hasattr(settings, 'RAZORPAY_WEBHOOK_SECRET') and settings.RAZORPAY_WEBHOOK_SECRET:
                client = razorpay.Client(auth=(settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET))
                try:
                    client.utility.verify_webhook_signature(
                        webhook_body, 
                        webhook_signature, 
                        settings.RAZORPAY_WEBHOOK_SECRET
                    )
                except razorpay.errors.SignatureVerificationError:
                    logger.error("Webhook signature verification failed")
                    return JsonResponse({'error': 'Invalid webhook signature'}, status=400)
            else:
                logger.warning("Webhook secret not configured - skipping signature verification")
            
            # Process webhook data
            data = json.loads(webhook_body)
            event = data.get('event')
            payload = data.get('payload', {})
            
            if event == 'payment.captured':
                # Handle successful payment
                payment_entity = payload.get('payment', {}).get('entity', {})
                order_id = payment_entity.get('order_id')
                
                if order_id:
                    try:
                        order = OrderDetail.objects.get(razorpay_order_id=order_id)
                        if not order.has_paid:
                            order.razorpay_payment_id = payment_entity.get('id')
                            order.has_paid = True
                            order.payment_status = 'completed'
                            order.save()
                            logger.info(f"Payment captured via webhook for order {order.id}")
                    except OrderDetail.DoesNotExist:
                        logger.error(f"Order not found for webhook payment: {order_id}")
            
            elif event == 'payment.failed':
                # Handle failed payment
                payment_entity = payload.get('payment', {}).get('entity', {})
                order_id = payment_entity.get('order_id')
                
                if order_id:
                    try:
                        order = OrderDetail.objects.get(razorpay_order_id=order_id)
                        order.payment_status = 'failed'
                        order.failure_reason = payment_entity.get('error_description', 'Payment failed')
                        order.save()
                        logger.info(f"Payment failed via webhook for order {order.id}")
                    except OrderDetail.DoesNotExist:
                        logger.error(f"Order not found for webhook payment failure: {order_id}")
            
            return JsonResponse({'status': 'success'})
            
        except json.JSONDecodeError:
            return JsonResponse({'error': 'Invalid JSON data'}, status=400)
        except Exception as e:
            logger.error(f"Webhook processing error: {str(e)}")
            return JsonResponse({'error': 'Webhook processing failed'}, status=500)
    
    return JsonResponse({'error': 'Invalid request method'}, status=405)


@login_required
@csrf_exempt
def add_to_wishlist(request, product_id):
    if request.method == 'POST':
        product = get_object_or_404(Product, id=product_id)
        # Assuming a Wishlist model with user & product fields
        if request.user.is_authenticated:
            Wishlist.objects.get_or_create(user=request.user, product=product)
            return JsonResponse({'success': True})
        else:
            return JsonResponse({'success': False, 'message': 'Login required'}, status=403)
    return JsonResponse({'success': False, 'message': 'Invalid request'}, status=400)


@login_required
def my_orders_view(request):
    # Debug: Check current user
    print(f"Current user: {request.user}")
    print(f"User email: {request.user.email}")
    print(f"User ID: {request.user.id}")
    
    # Debug: Check all orders for this user
    orders = OrderDetail.objects.filter(user=request.user).order_by('-created_on')
    print(f"Orders found: {orders.count()}")
    
    # Debug: Check orders by email instead
    orders_by_email = OrderDetail.objects.filter(customer_email=request.user.email)
    print(f"Orders by email: {orders_by_email.count()}")
    
    return render(request, 'mandii/my_orders.html', {'orders': orders})

# Add this to your views.py

@login_required
def create_product_view(request):
    """Create a new product"""
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES)
        if form.is_valid():
            product = form.save()
            messages.success(request, f'Product "{product.name}" created successfully!')
            return redirect('detail', id=product.id)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ProductForm()
    
    return render(request, 'mandii/create_product.html', {'form': form})

@login_required
def edit_product_view(request, id):
    """Edit an existing product"""
    product = get_object_or_404(Product, id=id)
    
    if request.method == 'POST':
        form = ProductForm(request.POST, request.FILES, instance=product)
        if form.is_valid():
            product = form.save()
            messages.success(request, f'Product "{product.name}" updated successfully!')
            return redirect('detail', id=product.id)
        else:
            messages.error(request, 'Please correct the errors below.')
    else:
        form = ProductForm(instance=product)
    
    return render(request, 'mandii/edit_product.html', {'form': form, 'product': product})

@login_required
def delete_product_view(request, id):
    """Delete a product"""
    product = get_object_or_404(Product, id=id)
    
    if request.method == 'POST':
        product_name = product.name
        product.delete()
        messages.success(request, f'Product "{product_name}" deleted successfully!')
        return redirect('index')
    
    return render(request, 'mandii/delete_product.html', {'product': product})

@login_required
def dashboard_view(request):
    """Dashboard view - shows overview of user's activity"""
    context = {
        'title': 'Dashboard',
    }
    return render(request, 'mandii/dashboard.html', context)

@login_required
def sales_view(request):
    """Sales view - shows user's sales data"""
    context = {
        'title': 'Sales',
    }
    return render(request, 'mandii/sales.html', context)

@login_required
def orders_view(request):
    """Orders view - shows all orders (different from my_orders)"""
    context = {
        'title': 'Orders',
        # Add orders data here
    }
    return render(request, 'mandii/orders.html', context)

@login_required
def profile_view(request):
    """User profile view"""
    context = {
        'title': 'Profile',
        'user': request.user,
    }
    return render(request, 'mandii/profile.html', context)

@login_required
def settings_view(request):
    """User settings view"""
    context = {
        'title': 'Settings',
    }
    return render(request, 'mandii/settings.html', context)

def login_view(request):
    """Custom login view"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            next_url = request.GET.get('next', 'index')
            return redirect(next_url)
        else:
            messages.error(request, 'Invalid username or password.')

    return render(request, 'mandii/login.html') 

def logout_view(request):
    """Logs out the user and redirects to login with a message."""
    logout(request)
    messages.success(request, "You have been logged out successfully.")
    return redirect('login')

# NEW: Registration view
def register_view(request):
    """User registration view"""
    if request.method == 'POST':
        form = UserCreationForm(request.POST)
        if form.is_valid():
            user = form.save()
            username = form.cleaned_data.get('username')
            messages.success(request, f'Account created for {username}! You can now log in.')
            return redirect('login')
        else:
            # Add form errors to messages
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f'{field}: {error}')
    else:
        form = UserCreationForm()
    
    return render(request, 'mandii/register.html', {'form': form})

# NEW: Custom registration view with email
def register_with_email_view(request):
    """User registration view with email validation"""
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password1 = request.POST.get('password1')
        password2 = request.POST.get('password2')
        
        # Basic validation
        if not all([username, email, password1, password2]):
            messages.error(request, 'All fields are required.')
            return render(request, 'mandii/register.html')
        
        if password1 != password2:
            messages.error(request, 'Passwords do not match.')
            return render(request, 'mandii/register.html')
        
        if len(password1) < 8:
            messages.error(request, 'Password must be at least 8 characters long.')
            return render(request, 'mandii/register.html')
        
        # Check if username already exists
        if User.objects.filter(username=username).exists():
            messages.error(request, 'Username already exists.')
            return render(request, 'mandii/register.html')
        
        # Check if email already exists
        if User.objects.filter(email=email).exists():
            messages.error(request, 'Email already registered.')
            return render(request, 'mandii/register.html')
        
        try:
            # Create user
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password1
            )
            messages.success(request, f'Account created for {username}! You can now log in.')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Error creating account: {str(e)}')
    
    return render(request, 'mandii/register.html')

# NEW: Password reset request view
def password_reset_request_view(request):
    """Password reset request view"""
    if request.method == 'POST':
        email = request.POST.get('email')
        
        if not email:
            messages.error(request, 'Email is required.')
            return render(request, 'mandii/password_reset_request.html')
        
        try:
            user = User.objects.get(email=email)
            
            # Generate token
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            
            # Create reset link
            current_site = get_current_site(request)
            reset_link = f"http://{current_site.domain}/password-reset-confirm/{uid}/{token}/"
            
            # Send email
            subject = 'Password Reset Request'
            message = f"""
            Hello {user.username},
            
            You requested a password reset. Click the link below to reset your password:
            {reset_link}
            
            If you didn't request this, please ignore this email.
            
            Best regards,
            Your Team
            """
            
            try:
                send_mail(
                    subject,
                    message,
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    fail_silently=False,
                )
                messages.success(request, 'Password reset email sent! Check your email.')
                return redirect('login')
            except Exception as e:
                messages.error(request, 'Error sending email. Please try again later.')
                logger.error(f"Email sending error: {str(e)}")
                
        except User.DoesNotExist:
            # Don't reveal if email exists or not for security
            messages.success(request, 'If an account with this email exists, you will receive a password reset email.')
            return redirect('login')
    
    return render(request, 'mandii/password_reset_request.html')

# NEW: Password reset confirm view
def password_reset_confirm_view(request, uidb64, token):
    """Password reset confirmation view"""
    try:
        uid = force_str(urlsafe_base64_decode(uidb64))
        user = User.objects.get(pk=uid)
    except (TypeError, ValueError, OverflowError, User.DoesNotExist):
        user = None
    
    if user is not None and default_token_generator.check_token(user, token):
        if request.method == 'POST':
            password1 = request.POST.get('password1')
            password2 = request.POST.get('password2')
            
            if not password1 or not password2:
                messages.error(request, 'Both password fields are required.')
                return render(request, 'mandii/password_reset_confirm.html', {
                    'validlink': True,
                    'user': user
                })
            
            if password1 != password2:
                messages.error(request, 'Passwords do not match.')
                return render(request, 'mandii/password_reset_confirm.html', {
                    'validlink': True,
                    'user': user
                })
            
            if len(password1) < 8:
                messages.error(request, 'Password must be at least 8 characters long.')
                return render(request, 'mandii/password_reset_confirm.html', {
                    'validlink': True,
                    'user': user
                })
            
            # Set new password
            user.set_password(password1)
            user.save()
            
            messages.success(request, 'Password reset successful! You can now log in with your new password.')
            return redirect('login')
        
        return render(request, 'mandii/password_reset_confirm.html', {
            'validlink': True,
            'user': user
        })
    else:
        messages.error(request, 'Invalid or expired password reset link.')
        return redirect('password_reset_request')