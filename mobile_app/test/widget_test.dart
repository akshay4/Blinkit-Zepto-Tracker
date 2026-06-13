import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:provider/provider.dart';
import 'package:mobile_app/main.dart';
import 'package:mobile_app/services/api_service.dart';

void main() {
  testWidgets('App launches and displays connection screen', (WidgetTester tester) async {
    // Build our app under MultiProvider with ApiService and trigger a frame.
    await tester.pumpWidget(
      MultiProvider(
        providers: [
          ChangeNotifierProvider(create: (_) => ApiService()),
        ],
        child: const MyApp(),
      ),
    );

    // Verify that connection UI elements are present
    expect(find.text('Connect to Host Server'), findsOneWidget);
    expect(find.text('Connect Dashboard'), findsOneWidget);
    expect(find.byIcon(Icons.dns_outlined), findsOneWidget);
  });
}
